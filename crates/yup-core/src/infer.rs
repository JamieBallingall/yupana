//! Step 3, Infer: the size of every expression, and its span along each dimension.
//!
//! A size is counted in rows and columns. A span says where a range sits along them: `tail x`
//! and `init x` have the same size but different spans, and only ranges with equal spans can be
//! combined position by position. A dimension of size 1 stretches to match the other operand, as
//! it does in the spreadsheet app.
//!
//! Every `a .. b` whose end is not known when the file is written introduces a new `Unknown`
//! size. A signature can name it, and the name is checked rather than trusted.
//!
//! `lag`, `tail` and `init` work along a range's one dimension when it has only one: across a
//! row, or down a column. A range of more than one row and column needs the direction written.
//!
//! Infer also works out what each cell holds, as the spreadsheet app's TYPE function tells them
//! apart: a number, text or a logical. A signature's type is checked against it.

use std::collections::{BTreeMap, BTreeSet};

use crate::csv::Field;
use crate::csv::Table;
use crate::diagnostic::{Diagnostic, Pos};
use crate::functions::{Builtin, Gives, Kind};
use crate::printer::type_text;
use crate::resolve::{Model, Recurrence, Symbol};
use crate::size::SizeExpr;
use crate::syntax::{
    Axis, BinOp, CellType, Expr, ExprKind, Ident, Program, Role, Signature, SizeKind, SizeSyntax,
    Type,
};

/// Where a range sits along one dimension: `size` positions, starting at position `start`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Extent {
    pub start: i64,
    pub size: SizeExpr,
}

impl Extent {
    fn one() -> Extent {
        Extent::of(SizeExpr::constant(1))
    }

    fn of(size: SizeExpr) -> Extent {
        Extent { start: 0, size }
    }
}

/// Where an expression sits along its rows and along its columns.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Shape {
    pub rows: Extent,
    pub columns: Extent,
}

impl Shape {
    fn scalar() -> Shape {
        Shape {
            rows: Extent::one(),
            columns: Extent::one(),
        }
    }
}

/// Whether a size variable is known when the file is written, and if so its value.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum VarKind {
    Fixed(i64),
    Unknown,
}

/// What is known of a size that a formula's data decides, beside the size it depends on.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Bound {
    /// At most that size: what `filter` gives.
    Maximum,
    /// As large as that size, but not lined up with it: what a sort gives.
    Same,
}

impl Bound {
    fn word(self) -> &'static str {
        match self {
            Bound::Maximum => "Maximum",
            Bound::Same => "Same",
        }
    }
}

/// A size variable, declared in the file or introduced by `a .. b`.
#[derive(Clone, Debug)]
pub struct Var {
    pub name: String,
    pub kind: VarKind,
    /// For a size the data decides, the most it can be, where that is known when the file is
    /// written: its cells are laid out, checked and styled as that many.
    pub bound: Option<i64>,
    /// For a size that no declaration names, the binding whose definition introduced it.
    pub introduced_by: Option<usize>,
}

/// A column of a table: its name, and what its cells hold, where that is known.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TableColumn {
    pub name: String,
    pub ty: Option<CellType>,
}

/// The shape and the cell type of every binding, in terms of the size variables.
#[derive(Clone, Debug)]
pub struct Typed {
    pub vars: Vec<Var>,
    pub shapes: Vec<Shape>,
    /// What each binding's cells hold, where that is one type.
    pub types: Vec<Option<CellType>>,
    /// The columns of each binding that is a table.
    pub tables: Vec<Option<Vec<TableColumn>>>,
    /// Which column each `table.name` takes, counted from 0, by its position.
    pub columns: BTreeMap<Pos, usize>,
    /// The parts of a recurrence that lie outside its cycle and run along it, by position:
    /// inside the scan that works the recurrence out, they are read one position at a time.
    pub along: BTreeSet<Pos>,
    /// For each value carried from one position to the next, the part outside its cycle whose
    /// length the scan runs over.
    pub drivers: BTreeMap<usize, Pos>,
    /// The way each carried value runs.
    pub carried: BTreeMap<usize, Axis>,
    /// The way each `lag`, `tail` and `init` outside a lambda works, by its position, where the
    /// file does not write it.
    pub axes: BTreeMap<Pos, Axis>,
}

impl Typed {
    /// The value of a fixed size variable.
    pub fn fixed(&self, var: usize) -> Option<i64> {
        match self.vars[var].kind {
            VarKind::Fixed(value) => Some(value),
            VarKind::Unknown => None,
        }
    }

    /// The value a size is laid out as: a fixed size's own, or the bound of a size the data
    /// decides.
    pub fn layout(&self, var: usize) -> Option<i64> {
        match self.vars[var].kind {
            VarKind::Fixed(value) => Some(value),
            VarKind::Unknown => self.vars[var].bound,
        }
    }

    /// A size as `.yup` writes it.
    pub fn format(&self, size: &SizeExpr) -> String {
        size.format(|var| self.vars[var].name.clone())
    }
}

type Inferred = Result<Shape, Option<Diagnostic>>;

fn fail(pos: Pos, message: impl Into<String>) -> Option<Diagnostic> {
    Some(Diagnostic::error(pos, message))
}

struct VarState {
    name: Option<String>,
    kind: VarKind,
    declared: bool,
    /// A size introduced by `..` and named by a signature stands for the declared size.
    alias: Option<usize>,
    /// For a declared unknown size, the introduced size that a signature named after it.
    named: Option<usize>,
    pos: Pos,
    binding: Option<usize>,
    /// For a size the data decides, what is known of it.
    bound: Option<Bounded>,
}

/// What is known of a size the data decides: the size it depends on, and how a message names
/// that size, as its declaration would write it.
#[derive(Clone)]
struct Bounded {
    bound: Bound,
    of: SizeExpr,
    text: String,
}

struct Infer<'a> {
    program: &'a Program,
    model: &'a Model,
    vars: Vec<VarState>,
    /// Each binding's shape, or `None` until inferred or when its definition is wrong.
    shapes: Vec<Option<Shape>>,
    errors: Vec<Diagnostic>,
    current: usize,
    /// The shapes of the parameters of the lambdas being called, innermost last. A lambda's
    /// body sees its own parameters only.
    params: Vec<BTreeMap<String, Shape>>,
    along: BTreeSet<Pos>,
    drivers: BTreeMap<usize, Pos>,
    carried: BTreeMap<usize, Axis>,
    axes: BTreeMap<Pos, Axis>,
    types: Vec<Option<CellType>>,
    tables: Vec<Option<Vec<TableColumn>>>,
    columns: BTreeMap<Pos, usize>,
    /// The bindings whose types could not be worked out, already reported.
    failed: BTreeSet<usize>,
    /// The columns of each row that `by_row` gives its lambda, by the parameter's name.
    rows: BTreeMap<String, Vec<TableColumn>>,
}

/// What a cell holds, as far as Infer can tell: one type, any of them, or none at all, which an
/// error such as `na` gives and which goes with anything.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Ty {
    Known(CellType),
    Any,
    Never,
}

fn join(a: Ty, b: Ty) -> Ty {
    match (a, b) {
        (Ty::Never, other) | (other, Ty::Never) => other,
        (a, b) if a == b => a,
        _ => Ty::Any,
    }
}

/// A table's columns as its type writes them, with `Any` for a column whose type is unknown.
fn show_columns(columns: &[TableColumn]) -> String {
    let columns: Vec<String> = columns
        .iter()
        .map(|column| {
            let ty = column.ty.map_or("Any", CellType::word);
            format!("{} : {ty}", column.name)
        })
        .collect();
    format!("{{ {} }}", columns.join(", "))
}

/// Infers the shape of every binding in `model`.
pub fn infer(program: &Program, model: &Model) -> Result<Typed, Vec<Diagnostic>> {
    let vars = model
        .sizes
        .iter()
        .map(|size| VarState {
            name: Some(size.name.clone()),
            kind: match size.kind {
                SizeKind::Fixed => {
                    VarKind::Fixed(size.value.expect("Resolve gives every fixed size a value"))
                }
                SizeKind::Unknown | SizeKind::Maximum(_) | SizeKind::Same(_) => VarKind::Unknown,
            },
            declared: true,
            alias: None,
            named: None,
            pos: size.pos,
            binding: None,
            bound: None,
        })
        .collect();
    let mut infer = Infer {
        program,
        model,
        vars,
        shapes: vec![None; model.bindings.len()],
        errors: Vec::new(),
        current: 0,
        params: Vec::new(),
        along: BTreeSet::new(),
        drivers: BTreeMap::new(),
        carried: BTreeMap::new(),
        axes: BTreeMap::new(),
        types: vec![None; model.bindings.len()],
        tables: vec![None; model.bindings.len()],
        columns: BTreeMap::new(),
        failed: BTreeSet::new(),
        rows: BTreeMap::new(),
    };
    for (var, size) in model.sizes.iter().enumerate() {
        let bound = match &size.kind {
            SizeKind::Maximum(_) => Bound::Maximum,
            SizeKind::Same(_) => Bound::Same,
            SizeKind::Fixed | SizeKind::Unknown => continue,
        };
        let written = size
            .kind
            .of()
            .expect("a size the data decides depends on another");
        match infer.depends_on(var, written) {
            Ok(of) => {
                infer.vars[var].bound = Some(Bounded {
                    bound,
                    of,
                    text: crate::printer::size(written),
                });
            }
            Err(error) => infer.errors.push(error),
        }
    }
    // A declaration that cannot be read would only mislead what follows.
    if !infer.errors.is_empty() {
        return Err(infer.errors);
    }
    for &binding in &model.order {
        match model.recurrence_of(binding) {
            // A recurrence's members come together in the order, and are inferred together.
            Some(recurrence) if recurrence.members[0] == binding => infer.recurrence(recurrence),
            Some(_) => {}
            None => infer.binding(binding),
        }
    }
    // What cannot be used by a formula has a shape of its own: a label is one cell, an empty
    // range is the size its signature gives, and styles, sheets and the workbook have no cells.
    for (binding, b) in model.bindings.iter().enumerate() {
        match b.role {
            Role::Label => {
                infer.current = binding;
                let sig = model.signature(program, binding);
                match infer.check_signature(sig, Shape::scalar()) {
                    Ok(shape) => infer.shapes[binding] = Some(shape),
                    Err(error) => infer.errors.push(error),
                }
                infer.types[binding] = Some(CellType::Text);
            }
            Role::Empty => {
                infer.current = binding;
                match infer.empty_shape(model.signature(program, binding)) {
                    Ok(shape) => infer.shapes[binding] = Some(shape),
                    Err(error) => infer.errors.push(error),
                }
            }
            Role::Style | Role::Sheet | Role::Workbook => {
                infer.shapes[binding] = Some(Shape::scalar());
            }
            _ => {}
        }
    }
    for &binding in &model.order {
        infer.cell_type(binding);
    }
    infer.finish()
}

impl Infer<'_> {
    fn canonical(&self, var: usize) -> usize {
        self.vars[var].alias.unwrap_or(var)
    }

    /// A size with aliases followed and fixed sizes replaced by their values, so that equal
    /// sizes compare equal.
    fn normal(&self, size: &SizeExpr) -> SizeExpr {
        size.rename(|var| self.canonical(var))
            .substitute(|var| match self.vars[var].kind {
                VarKind::Fixed(value) => Some(value),
                VarKind::Unknown => None,
            })
    }

    fn same(&self, a: &SizeExpr, b: &SizeExpr) -> bool {
        self.normal(a) == self.normal(b)
    }

    fn is_one(&self, size: &SizeExpr) -> bool {
        self.normal(size).as_constant() == Some(1)
    }

    fn is_scalar(&self, shape: &Shape) -> bool {
        self.is_one(&shape.rows.size) && self.is_one(&shape.columns.size)
    }

    fn var_name(&self, var: usize) -> String {
        let var = self.canonical(var);
        match (&self.vars[var].name, self.vars[var].binding) {
            (Some(name), _) => name.clone(),
            (None, Some(binding)) => format!("{}_n", self.model.bindings[binding].name),
            (None, None) => unreachable!("an introduced size has a binding"),
        }
    }

    fn show(&self, size: &SizeExpr) -> String {
        size.rename(|var| self.canonical(var))
            .format(|var| self.var_name(var))
    }

    fn positions(&self, extent: &Extent) -> String {
        format!(
            "[{}, {})",
            extent.start,
            self.show(&extent.size.offset(extent.start))
        )
    }

    fn fresh(&mut self, pos: Pos) -> SizeExpr {
        self.vars.push(VarState {
            name: None,
            kind: VarKind::Unknown,
            declared: false,
            alias: None,
            named: None,
            pos,
            binding: Some(self.current),
            bound: None,
        });
        SizeExpr::var(self.vars.len() - 1)
    }

    /// A new size that the data decides, which a declaration must name.
    fn fresh_bounded(&mut self, pos: Pos, bound: Bound, of: SizeExpr, text: String) -> SizeExpr {
        let size = self.fresh(pos);
        let var = self.vars.len() - 1;
        self.vars[var].bound = Some(Bounded { bound, of, text });
        size
    }

    /// How a declaration names the rows of `from`. A table read from a file has as many rows as
    /// it has records, a number; a declaration names them after the table, so a message does
    /// too.
    fn rows_text(&self, from: &Expr, rows: &SizeExpr) -> String {
        match &from.kind {
            ExprKind::Name(name)
                if matches!(self.model.symbols.get(name),
                    Some(Symbol::Binding(binding)) if self.model.tables.contains_key(binding)) =>
            {
                format!("{name}_n")
            }
            _ => self.show(rows),
        }
    }

    /// Checks that `key` is one column lined up with the rows of `table`, as `filter` and the
    /// sorts take.
    fn lined_up_column(&self, key: &Expr, keys: &Shape, table: &Shape, what: &str) -> Inferred {
        let lined_up = self.is_one(&keys.columns.size)
            && self.same(&keys.rows.size, &table.rows.size)
            && keys.rows.start == table.rows.start;
        if lined_up {
            return Ok(table.clone());
        }
        let this = if self.is_one(&keys.columns.size) {
            format!("this covers rows {}", self.positions(&keys.rows))
        } else {
            format!("this has {} columns", self.show(&keys.columns.size))
        };
        Err(fail(
            key.pos,
            format!(
                "{what}: the table covers rows {}, and {this}",
                self.positions(&table.rows)
            ),
        ))
    }

    /// The size that the declared size `var` depends on, as its declaration writes it: sizes
    /// declared above it, and a table read from a file by its rows, `sales_n`.
    fn depends_on(&self, var: usize, syntax: &SizeSyntax) -> Result<SizeExpr, Diagnostic> {
        let mut constant = syntax.constant;
        let mut terms = Vec::new();
        for (coefficient, ident) in &syntax.terms {
            match self.model.symbols.get(&ident.name) {
                Some(Symbol::Size(other)) if *other < var => {
                    terms.push((*other, *coefficient));
                    continue;
                }
                Some(Symbol::Size(_)) => {
                    return Err(Diagnostic::error(
                        ident.pos,
                        format!(
                            "`{}` is declared below; a size the data decides depends on sizes \
                             declared above it",
                            ident.name
                        ),
                    ));
                }
                _ => {
                    let rows = ident
                        .name
                        .strip_suffix("_n")
                        .and_then(|table| match self.model.symbols.get(table) {
                            Some(Symbol::Binding(binding)) => self.model.tables.get(binding),
                            _ => None,
                        })
                        .map(|table| count(table.rows.len()));
                    let Some(rows) = rows else {
                        return Err(Diagnostic::error(
                            ident.pos,
                            format!(
                                "unknown size `{}`; a size the data decides depends on a size \
                                 declared above it, or on the rows of a table read from a file, \
                                 as `sales_n` for `sales`",
                                ident.name
                            ),
                        ));
                    };
                    constant += coefficient * rows;
                }
            }
        }
        Ok(SizeExpr::from_parts(constant, terms))
    }

    /// How a size the data decides is declared, for a message: `Size Maximum 20`.
    fn bound_text(bounded: &Bounded) -> String {
        format!("Size {} {}", bounded.bound.word(), bounded.text)
    }

    fn binding(&mut self, binding: usize) {
        self.current = binding;
        // A lambda has no shape of its own; each call is inferred where it is made.
        if self.model.bindings[binding].role == Role::Lambda {
            self.shapes[binding] = Some(Shape::scalar());
            return;
        }
        let expr = self.model.definition(self.program, binding);
        let shape = match self.shape(expr) {
            Ok(shape) => shape,
            Err(error) => {
                self.errors.extend(error);
                return;
            }
        };
        let sig = self.model.signature(self.program, binding);
        let shape = match self.check_signature(sig, shape) {
            Ok(shape) => shape,
            Err(error) => {
                self.errors.push(error);
                return;
            }
        };
        self.shapes[binding] = Some(shape);
    }

    fn shape(&mut self, expr: &Expr) -> Inferred {
        match &expr.kind {
            ExprKind::Number { .. }
            | ExprKind::Str(_)
            | ExprKind::Bool(_)
            | ExprKind::Text(_)
            | ExprKind::Constructor(_)
            | ExprKind::Update { .. }
            | ExprKind::Lambda { .. } => Ok(Shape::scalar()),
            // HSTACK pads a shorter column with #N/A rather than stretching it, so every column
            // has the same rows, by size and span.
            ExprKind::Record(fields) => {
                let mut rows: Option<(Extent, &Ident)> = None;
                for field in fields {
                    let shape = self.shape(&field.value)?;
                    if !self.is_one(&shape.columns.size) {
                        return Err(fail(
                            field.value.pos,
                            format!(
                                "a column of a table is one column, and `{}` has {}",
                                field.name.name,
                                self.show(&shape.columns.size)
                            ),
                        ));
                    }
                    match &rows {
                        None => rows = Some((shape.rows, &field.name)),
                        Some((first, first_name)) => {
                            let problem = if self.same(&first.size, &shape.rows.size) {
                                (first.start != shape.rows.start).then(|| {
                                    format!(
                                        "cover different rows: positions {} and {}; `lag` \
                                         shifts a range along, and `tail` and `init` drop its \
                                         ends",
                                        self.positions(first),
                                        self.positions(&shape.rows)
                                    )
                                })
                            } else {
                                Some(format!(
                                    "have different numbers of rows: {} and {}",
                                    self.show(&first.size),
                                    self.show(&shape.rows.size)
                                ))
                            };
                            if let Some(problem) = problem {
                                return Err(fail(
                                    field.value.pos,
                                    format!(
                                        "the columns `{}` and `{}` of a table {problem}",
                                        first_name.name, field.name.name
                                    ),
                                ));
                            }
                        }
                    }
                }
                let (rows, _) = rows.expect("Resolve gives a table at least one column");
                Ok(Shape {
                    rows,
                    columns: Extent::of(SizeExpr::constant(count(fields.len()))),
                })
            }
            ExprKind::Column { table, .. } => {
                let table = self.shape(table)?;
                Ok(Shape {
                    rows: table.rows,
                    columns: Extent::one(),
                })
            }
            ExprKind::Name(name)
                if self
                    .params
                    .last()
                    .is_some_and(|frame| frame.contains_key(name)) =>
            {
                Ok(self.params.last().expect("checked just now")[name].clone())
            }
            ExprKind::Name(name) => match self.model.symbols.get(name) {
                // A binding whose definition is wrong has been reported; what uses it is not.
                Some(Symbol::Binding(binding)) => self.shapes[*binding].clone().ok_or(None),
                Some(Symbol::Size(_)) => Ok(Shape::scalar()),
                // A function of no arguments, such as `na`.
                None => Ok(Shape::scalar()),
            },
            ExprKind::Neg(operand) => self.shape(operand),
            ExprKind::Binary { op, left, right } => {
                let left = self.shape(left)?;
                let right = self.shape(right)?;
                self.combine(&left, &right).map_err(|problem| {
                    fail(
                        expr.pos,
                        format!("the two sides of `{}` {problem}", op.symbol()),
                    )
                })
            }
            ExprKind::If { cond, then, other } => {
                let cond = self.shape(cond)?;
                let then = self.shape(then)?;
                let other = self.shape(other)?;
                let branches = self.combine(&then, &other).map_err(|problem| {
                    fail(expr.pos, format!("the two branches of `if` {problem}"))
                })?;
                self.combine(&cond, &branches).map_err(|problem| {
                    fail(
                        expr.pos,
                        format!("the condition and the branches of `if` {problem}"),
                    )
                })
            }
            ExprKind::List(rows) => {
                let width = rows[0].len();
                if let Some(row) = rows.iter().find(|row| row.len() != width) {
                    return Err(fail(
                        row[0].pos,
                        format!(
                            "every row of a list has the same number of values; the first has \
                             {width}"
                        ),
                    ));
                }
                if let Some(value) = rows.iter().flatten().find(|value| !value.is_literal()) {
                    return Err(fail(
                        value.pos,
                        "a list holds literal values: numbers, strings, `true` and `false`",
                    ));
                }
                Ok(Shape {
                    rows: Extent::of(SizeExpr::constant(count(rows.len()))),
                    columns: Extent::of(SizeExpr::constant(count(width))),
                })
            }
            ExprKind::Range { first, last } => self.range(expr, first, last),
            ExprKind::Apply {
                func,
                axis,
                args,
                seed,
            } => match Builtin::lookup(&func.name) {
                Some(builtin) => self.apply(expr, builtin, *axis, args, seed.as_deref()),
                None => self.call(func, args),
            },
        }
    }

    /// The shape of `map`, `by_row`, `by_column`, `reduce` or `scan`, whose lambda gives one
    /// value each time it is called. In the app, a lambda that gives a range shows `#CALC!` when
    /// the workbook calculates; here it is refused where it is written.
    fn lambda_call(&mut self, builtin: Builtin, args: &[Expr]) -> Option<Inferred> {
        let name = builtin.name();
        let (params, result): (Vec<(Shape, String)>, Shape) = match builtin {
            Builtin::Map | Builtin::ByRow | Builtin::ByColumn => {
                let x = match self.shape(&args[1]) {
                    Ok(x) => x,
                    Err(error) => return Some(Err(error)),
                };
                let of = crate::printer::expression(&args[1]);
                match builtin {
                    Builtin::Map => (
                        vec![(Shape::scalar(), format!("one value of `{of}`"))],
                        x.clone(),
                    ),
                    Builtin::ByRow => (
                        vec![(
                            Shape {
                                rows: Extent::one(),
                                columns: Extent::of(x.columns.size.clone()),
                            },
                            format!("one row of `{of}`"),
                        )],
                        Shape {
                            rows: x.rows.clone(),
                            columns: Extent::one(),
                        },
                    ),
                    _ => (
                        vec![(
                            Shape {
                                rows: Extent::of(x.rows.size.clone()),
                                columns: Extent::one(),
                            },
                            format!("one column of `{of}`"),
                        )],
                        Shape {
                            rows: Extent::one(),
                            columns: x.columns.clone(),
                        },
                    ),
                }
            }
            Builtin::Reduce | Builtin::Scan => {
                let first = match self.shape(&args[1]) {
                    Ok(first) => first,
                    Err(error) => return Some(Err(error)),
                };
                if !self.is_scalar(&first) {
                    return Some(Err(fail(
                        args[1].pos,
                        format!("the first value of `{name}` is one value"),
                    )));
                }
                let x = match self.shape(&args[2]) {
                    Ok(x) => x,
                    Err(error) => return Some(Err(error)),
                };
                let of = crate::printer::expression(&args[2]);
                let gives = if builtin == Builtin::Scan {
                    x.clone()
                } else {
                    Shape::scalar()
                };
                (
                    vec![
                        (Shape::scalar(), "the value so far".to_string()),
                        (Shape::scalar(), format!("one value of `{of}`")),
                    ],
                    gives,
                )
            }
            _ => return None,
        };
        let function = &args[0];
        let (names, body, outer): (Vec<String>, &Expr, bool) = match &function.kind {
            ExprKind::Lambda { params, body } => (
                params.iter().map(|param| param.name.clone()).collect(),
                body,
                true,
            ),
            ExprKind::Name(lambda) => {
                let Some(&Symbol::Binding(binding)) = self.model.symbols.get(lambda) else {
                    unreachable!("Resolve checks the function's name");
                };
                (
                    self.model.bindings[binding].params.clone(),
                    self.model.definition(self.program, binding),
                    false,
                )
            }
            _ => unreachable!("Resolve checks that the function is a lambda or a `Lambda`"),
        };
        // An inline lambda also sees the parameters of the lambdas around it; a `Lambda` of the
        // file sees its own only.
        let mut frame = if outer {
            self.params.last().cloned().unwrap_or_default()
        } else {
            BTreeMap::new()
        };
        for (name, (shape, _)) in names.iter().zip(&params) {
            frame.insert(name.clone(), shape.clone());
        }
        self.params.push(frame);
        let gives = self.shape(body);
        let problem = match &gives {
            Ok(gives) if !self.is_scalar(gives) => {
                let described: Vec<(String, String)> = names
                    .iter()
                    .cloned()
                    .zip(params.iter().map(|(_, what)| what.clone()))
                    .collect();
                Some(self.one_value_problem(name, gives, body, &described))
            }
            _ => None,
        };
        self.params.pop();
        Some(match (gives, problem) {
            (Err(error), _) => Err(error),
            (Ok(_), Some(problem)) => Err(fail(function.pos, problem)),
            (Ok(_), None) => Ok(result),
        })
    }

    /// Why a lambda that gives a range is refused, naming where each range in its body comes
    /// from: a parameter, and what the function gives it, or a binding outside the lambda.
    fn one_value_problem(
        &mut self,
        func: &str,
        gives: &Shape,
        body: &Expr,
        params: &[(String, String)],
    ) -> String {
        let mut parts: Vec<String> = Vec::new();
        let mut stack = vec![body];
        while let Some(expr) = stack.pop() {
            let shown = crate::printer::expression(expr);
            let part = match &expr.kind {
                ExprKind::Name(name) => match params.iter().find(|(param, _)| param == name) {
                    Some((_, what)) => Some(format!("`{name}` is {what}")),
                    None => self.range_part(expr, &shown),
                },
                ExprKind::Column { .. } => self.range_part(expr, &shown),
                _ => {
                    stack.extend(expr.children().into_iter().rev());
                    None
                }
            };
            if let Some(part) = part
                && !parts.contains(&part)
            {
                parts.push(part);
            }
        }
        let mut message = format!(
            "the lambda given to `{func}` gives {}, not one value",
            self.shape_text(gives)
        );
        if !parts.is_empty() {
            message.push_str(": ");
            message.push_str(&parts.join(", and "));
        }
        message
    }

    /// How a range from outside a lambda is named in a message about the lambda, if it is one.
    fn range_part(&mut self, expr: &Expr, shown: &str) -> Option<String> {
        let shape = self.shape(expr).ok()?;
        (!self.is_scalar(&shape)).then(|| {
            format!(
                "`{shown}` has {}, from outside the lambda",
                self.shape_text(&shape)
            )
        })
    }

    /// A range's size in words: `sales_n rows`, `3 columns`, or both.
    fn shape_text(&self, shape: &Shape) -> String {
        let (rows, columns) = (self.show(&shape.rows.size), self.show(&shape.columns.size));
        match (
            self.is_one(&shape.rows.size),
            self.is_one(&shape.columns.size),
        ) {
            (true, true) => "one value".to_string(),
            (false, true) => format!("{rows} rows"),
            (true, false) => format!("{columns} columns"),
            (false, false) => format!("{rows} rows and {columns} columns"),
        }
    }

    /// A call of a lambda of the file's own. Its body is inferred with its parameters taking the
    /// shapes of the arguments, so each call has the shape its arguments give it.
    fn call(&mut self, func: &Ident, args: &[Expr]) -> Inferred {
        let Some(&Symbol::Binding(binding)) = self.model.symbols.get(&func.name) else {
            unreachable!("Resolve checks function names");
        };
        let mut frame = BTreeMap::new();
        for (param, arg) in self.model.bindings[binding].params.iter().zip(args) {
            frame.insert(param.clone(), self.shape(arg)?);
        }
        self.params.push(frame);
        let body = self.model.definition(self.program, binding);
        let shape = self.shape(body);
        self.params.pop();
        shape.map_err(|error| {
            error.map(|mut error| {
                error.message.push_str(&format!(
                    ", in `{}` as it is called at {}",
                    func.name, func.pos
                ));
                error
            })
        })
    }

    fn range(&mut self, expr: &Expr, first: &Expr, last: &Expr) -> Inferred {
        for end in [first, last] {
            let shape = self.shape(end)?;
            if !self.is_scalar(&shape) {
                return Err(fail(end.pos, "each end of `..` is a single value"));
            }
        }
        let Some(start) = first.integer() else {
            return Err(fail(
                first.pos,
                "a range starts at a whole number written in the file, such as `1 .. n`",
            ));
        };
        let end = match (&last.kind, last.integer()) {
            (_, Some(end)) => Some(SizeExpr::constant(end)),
            (ExprKind::Name(name), None) => match self.model.symbols.get(name) {
                Some(Symbol::Size(var)) => Some(SizeExpr::var(*var)),
                _ => None,
            },
            _ => None,
        };
        let size = match end {
            Some(end) => {
                let size = end.offset(1 - start);
                if self.normal(&size).as_constant().is_some_and(|n| n < 1) {
                    return Err(fail(expr.pos, "this range has no numbers in it"));
                }
                size
            }
            // The end is decided in the workbook, so the length is too.
            None => self.fresh(expr.pos),
        };
        Ok(Shape {
            rows: Extent::one(),
            columns: Extent::of(size),
        })
    }

    /// The way `lag`, `tail` or `init` works on `x`: as written, or along `x`'s one dimension.
    fn axis(
        &mut self,
        x: &Shape,
        written: Option<Axis>,
        pos: Pos,
        what: &str,
    ) -> Result<Axis, Option<Diagnostic>> {
        if let Some(axis) = written {
            return Ok(axis);
        }
        // A lambda's body is written once into the workbook, however it is called, so it says
        // which way it works.
        if !self.params.is_empty() {
            return Err(fail(
                pos,
                format!(
                    "inside a lambda, `{what}` says which way it works, `across` or `down`, as in \
                     `{what} across x`: the lambda may be given rows or columns"
                ),
            ));
        }
        let axis = match (self.is_one(&x.rows.size), self.is_one(&x.columns.size)) {
            (false, true) => Axis::Down,
            (true, _) => Axis::Across,
            (false, false) => {
                return Err(fail(
                    pos,
                    format!(
                        "`{what}` works across or down, and this range has more than one row and \
                         column: say which, as in `{what} across x`"
                    ),
                ));
            }
        };
        self.axes.insert(pos, axis);
        Ok(axis)
    }

    fn apply(
        &mut self,
        expr: &Expr,
        builtin: Builtin,
        written: Option<Axis>,
        args: &[Expr],
        seed: Option<&Expr>,
    ) -> Inferred {
        if let Some(shape) = self.lambda_call(builtin, args) {
            return shape;
        }
        match builtin.kind() {
            Kind::Positionwise { fixed } => return self.positionwise(expr, builtin, fixed, args),
            Kind::Reduce { fixed } => {
                self.positionwise(expr, builtin, fixed, args)?;
                return Ok(Shape::scalar());
            }
            Kind::Own => {}
        }
        match builtin {
            Builtin::Lag => {
                let steps = steps(&args[0], "lag")?;
                let x = self.shape(&args[1])?;
                let axis = self.axis(&x, written, expr.pos, "lag")?;
                let Some(seed) = seed else {
                    return self.shift(&x, axis, steps, steps, expr.pos, "lag");
                };
                if steps != 1 {
                    return Err(fail(
                        seed.pos,
                        "`seed` gives one position of first values, so it goes with `lag 1`",
                    ));
                }
                let first = self.shape(seed)?;
                let (across, along) = match axis {
                    Axis::Across => (&first.columns, (&first.rows, &x.rows, "rows")),
                    Axis::Down => (&first.rows, (&first.columns, &x.columns, "columns")),
                };
                if !self.is_one(&across.size) {
                    return Err(fail(
                        seed.pos,
                        "the first values after `seed` are one position of the range",
                    ));
                }
                let (seed_extent, x_extent, dimension) = along;
                if !self.is_one(&seed_extent.size) && !self.same(&seed_extent.size, &x_extent.size)
                {
                    return Err(fail(
                        seed.pos,
                        format!(
                            "the first values after `seed` have {} {dimension}, and the range has {}",
                            self.show(&seed_extent.size),
                            self.show(&x_extent.size)
                        ),
                    ));
                }
                Ok(x)
            }
            Builtin::Tail => {
                let x = self.shape(&args[0])?;
                let axis = self.axis(&x, written, expr.pos, "tail")?;
                self.shift(&x, axis, 1, 1, expr.pos, "tail")
            }
            Builtin::First | Builtin::Last => {
                let what = builtin.name();
                let count = steps(&args[0], what)?;
                let x = self.shape(&args[1])?;
                let axis = self.axis(&x, written, expr.pos, what)?;
                let (along, dimension) = match axis {
                    Axis::Across => (&x.columns, "columns"),
                    Axis::Down => (&x.rows, "rows"),
                };
                // The range has at least this many positions, with each unknown size at its
                // smallest, 1; were it shorter, the app would give fewer than `count`.
                let size = self.normal(&along.size);
                let fewest = size.evaluate(|_| 1);
                if count > fewest {
                    let may = if size.as_constant().is_none() {
                        format!(", which may be as few as {fewest}")
                    } else {
                        String::new()
                    };
                    return Err(fail(
                        args[0].pos,
                        format!(
                            "`{what} {count}` takes {count} {dimension}, and the range has {}{may}",
                            self.show(&along.size)
                        ),
                    ));
                }
                let taken = Extent::of(SizeExpr::constant(count));
                Ok(match axis {
                    Axis::Across => Shape {
                        rows: x.rows,
                        columns: taken,
                    },
                    Axis::Down => Shape {
                        rows: taken,
                        columns: x.columns,
                    },
                })
            }
            Builtin::Init => {
                let x = self.shape(&args[0])?;
                let axis = self.axis(&x, written, expr.pos, "init")?;
                self.shift(&x, axis, 0, 1, expr.pos, "init")
            }
            Builtin::Na => Ok(Shape::scalar()),
            Builtin::SumColumns => {
                let x = self.shape(&args[0])?;
                Ok(Shape {
                    rows: Extent::one(),
                    columns: x.columns,
                })
            }
            Builtin::SumRows => {
                let x = self.shape(&args[0])?;
                Ok(Shape {
                    rows: x.rows,
                    columns: Extent::one(),
                })
            }
            Builtin::Csv => {
                let table = &self.model.tables[&self.current];
                if table.rows.is_empty() {
                    return Err(fail(args[0].pos, "the file has column names but no rows"));
                }
                Ok(Shape {
                    rows: Extent::of(SizeExpr::constant(count(table.rows.len()))),
                    columns: Extent::of(SizeExpr::constant(count(table.header.len()))),
                })
            }
            Builtin::Transpose => {
                let x = self.shape(&args[0])?;
                Ok(Shape {
                    rows: x.columns,
                    columns: x.rows,
                })
            }
            Builtin::Filter | Builtin::SortBy | Builtin::SortByDescending => {
                let keys = self.shape(&args[0])?;
                let table = self.shape(&args[1])?;
                let (bound, what) = match builtin {
                    Builtin::Filter => (
                        Bound::Maximum,
                        "`filter` keeps the rows where one column lined up with them holds",
                    ),
                    _ => (
                        Bound::Same,
                        "a sort orders the rows by one column lined up with them",
                    ),
                };
                let table = self.lined_up_column(&args[0], &keys, &table, what)?;
                let text = self.rows_text(&args[1], &table.rows.size);
                let rows = self.fresh_bounded(expr.pos, bound, table.rows.size, text);
                Ok(Shape {
                    rows: Extent::of(rows),
                    columns: Extent::of(table.columns.size),
                })
            }
            Builtin::Unique => {
                let table = self.shape(&args[0])?;
                let text = self.rows_text(&args[0], &table.rows.size);
                let rows = self.fresh_bounded(expr.pos, Bound::Maximum, table.rows.size, text);
                Ok(Shape {
                    rows: Extent::of(rows),
                    columns: Extent::of(table.columns.size),
                })
            }
            Builtin::Stack => {
                let first = self.shape(&args[0])?;
                let second = self.shape(&args[1])?;
                if !self.same(&first.columns.size, &second.columns.size) {
                    return Err(fail(
                        args[1].pos,
                        format!(
                            "`stack` takes two ranges with the same columns, and these have {} \
                             and {}",
                            self.show(&first.columns.size),
                            self.show(&second.columns.size)
                        ),
                    ));
                }
                let text = format!(
                    "{} + {}",
                    self.rows_text(&args[0], &first.rows.size),
                    self.rows_text(&args[1], &second.rows.size)
                );
                let of = first.rows.size.plus(&second.rows.size);
                let rows = self.fresh_bounded(expr.pos, Bound::Same, of, text);
                Ok(Shape {
                    rows: Extent::of(rows),
                    columns: Extent::of(first.columns.size),
                })
            }
            Builtin::Lookup => {
                let key = self.shape(&args[0])?;
                let keys = self.shape(&args[1])?;
                let values = self.shape(&args[2])?;
                if !self.is_one(&keys.rows.size) && !self.is_one(&keys.columns.size) {
                    return Err(fail(
                        args[1].pos,
                        "the keys of `lookup` are one row or one column",
                    ));
                }
                if !self.same_shape(&keys, &values) {
                    return Err(fail(
                        args[2].pos,
                        "the values of `lookup` have the same size and span as its keys",
                    ));
                }
                // One result for each key looked up, where the key is.
                Ok(key)
            }
            _ => unreachable!("every function whose kind is its own has a rule here"),
        }
    }

    /// A function that works position by position: its first `fixed` arguments are single
    /// values, and the rest combine as an operator's operands do.
    fn positionwise(
        &mut self,
        expr: &Expr,
        builtin: Builtin,
        fixed: usize,
        args: &[Expr],
    ) -> Inferred {
        for arg in &args[..fixed] {
            let shape = self.shape(arg)?;
            if !self.is_scalar(&shape) {
                return Err(fail(
                    arg.pos,
                    format!(
                        "this argument of `{}` is one value, as in `{}`",
                        builtin.name(),
                        builtin.usage()
                    ),
                ));
            }
        }
        let data = &args[fixed..];
        let mut shape = self.shape(&data[0])?;
        for arg in &data[1..] {
            let next = self.shape(arg)?;
            shape = self.combine(&shape, &next).map_err(|problem| {
                let which = if data.len() == 2 { "two " } else { "" };
                fail(
                    expr.pos,
                    format!(
                        "the {which}arguments of `{}` {problem}",
                        function_name(expr)
                    ),
                )
            })?;
        }
        Ok(shape)
    }

    /// Infers a recurrence, in two passes. First the carried value, where it is lagged, is taken
    /// as a single value, which stretches to fit anything: that gives it its shape from what lies
    /// outside the cycle. Then every member is inferred again, with that shape for the carried
    /// value and with its signature checked, and it must come out the same.
    fn recurrence(&mut self, recurrence: &Recurrence) {
        let state = recurrence.state;
        let name = &self.model.bindings[state].name;
        let pos = self.model.bindings[state].pos;
        let vars = self.vars.len();
        let errors = self.errors.len();
        let fail = |infer: &mut Self, message: String| {
            infer.errors.push(Diagnostic::error(pos, message));
            for &member in &recurrence.members {
                infer.shapes[member] = None;
            }
        };
        self.shapes[state] = Some(Shape::scalar());
        for &member in &recurrence.members {
            self.current = member;
            match self.shape(self.model.definition(self.program, member)) {
                Ok(shape) => self.shapes[member] = Some(shape),
                Err(error) => {
                    self.errors.extend(error);
                    return fail(self, format!("`{name}` cannot be worked out"));
                }
            }
        }
        if self.vars.len() != vars {
            self.vars.truncate(vars);
            return fail(
                self,
                format!(
                    "a range made with `..` cannot be inside the cycle that carries `{name}`; \
                     make it a binding of its own, outside"
                ),
            );
        }
        let assumed = self.shapes[state].clone().expect("the balance has a shape");
        for &member in &recurrence.members {
            self.binding(member);
        }
        if self.errors.len() != errors {
            return fail(self, format!("`{name}` cannot be worked out"));
        }
        let settled = self.shapes[state].clone().expect("the balance has a shape");
        if !self.same_shape(&assumed, &settled) {
            return fail(self, format!("the size of `{name}` does not settle"));
        }
        let axis = match (
            self.is_one(&settled.rows.size),
            self.is_one(&settled.columns.size),
        ) {
            (true, _) => Axis::Across,
            (false, true) => Axis::Down,
            (false, false) => {
                return fail(
                    self,
                    format!(
                        "a value carried from one position to the next is one row or one column, \
                         and `{name}` has {} rows and {} columns",
                        self.show(&settled.rows.size),
                        self.show(&settled.columns.size)
                    ),
                );
            }
        };
        let members: BTreeSet<usize> = recurrence.members.iter().copied().collect();
        let mut driver = None;
        for &member in &recurrence.members {
            let definition = self.model.definition(self.program, member);
            self.outside_along(definition, &members, axis, &mut driver);
        }
        match driver {
            Some(driver) => {
                self.drivers.insert(state, driver);
                self.carried.insert(state, axis);
            }
            None => fail(
                self,
                format!(
                    "nothing outside the cycle gives `{name}` its length: it needs a row or a \
                     column it uses position by position, such as a timeline"
                ),
            ),
        }
    }

    /// Whether an expression uses a binding in `members`.
    fn mentions(&self, expr: &Expr, members: &BTreeSet<usize>) -> bool {
        match &expr.kind {
            ExprKind::Name(name) => matches!(
                self.model.symbols.get(name),
                Some(Symbol::Binding(binding)) if members.contains(binding)
            ),
            _ => expr
                .children()
                .into_iter()
                .any(|child| self.mentions(child, members)),
        }
    }

    /// Finds the largest parts of a member's definition that lie outside the cycle, and records
    /// those that run along it. The first is the one whose length the scan runs over.
    fn outside_along(
        &mut self,
        expr: &Expr,
        members: &BTreeSet<usize>,
        axis: Axis,
        driver: &mut Option<Pos>,
    ) {
        if !self.mentions(expr, members) {
            if let Ok(shape) = self.shape(expr) {
                let length = match axis {
                    Axis::Across => &shape.columns.size,
                    Axis::Down => &shape.rows.size,
                };
                if !self.is_one(length) {
                    self.along.insert(expr.pos);
                    driver.get_or_insert(expr.pos);
                }
            }
            return;
        }
        match &expr.kind {
            // The value a position back is the scan's own; its first value starts the scan.
            ExprKind::Apply { func, .. } if func.name == "lag" => {}
            _ => {
                for child in expr.children() {
                    self.outside_along(child, members, axis, driver);
                }
            }
        }
    }

    fn same_shape(&self, a: &Shape, b: &Shape) -> bool {
        let same = |a: &Extent, b: &Extent| a.start == b.start && self.same(&a.size, &b.size);
        same(&a.rows, &b.rows) && same(&a.columns, &b.columns)
    }

    /// Drops `dropped` positions along `axis` and moves the start along by `start`: `lag k`
    /// moves by `k`, `tail` by 1, and `init` stays put.
    fn shift(
        &self,
        x: &Shape,
        axis: Axis,
        start: i64,
        dropped: i64,
        pos: Pos,
        what: &str,
    ) -> Inferred {
        let (along, dimension) = match axis {
            Axis::Across => (&x.columns, "columns"),
            Axis::Down => (&x.rows, "rows"),
        };
        let size = along.size.offset(-dropped);
        if self.normal(&size).as_constant().is_some_and(|n| n < 1) {
            return Err(fail(
                pos,
                format!(
                    "`{what}` leaves no {dimension} of a range that has {}",
                    self.show(&along.size)
                ),
            ));
        }
        let shifted = Extent {
            start: along.start + start,
            size,
        };
        Ok(match axis {
            Axis::Across => Shape {
                rows: x.rows.clone(),
                columns: shifted,
            },
            Axis::Down => Shape {
                rows: shifted,
                columns: x.columns.clone(),
            },
        })
    }

    fn combine(&self, a: &Shape, b: &Shape) -> Result<Shape, String> {
        Ok(Shape {
            rows: self.combine_extent(&a.rows, &b.rows, "rows")?,
            columns: self.combine_extent(&a.columns, &b.columns, "columns")?,
        })
    }

    /// Combines two operands along one dimension. The result reads after "the two sides of
    /// `+`" when it is a problem.
    fn combine_extent(&self, a: &Extent, b: &Extent, axis: &str) -> Result<Extent, String> {
        match (self.is_one(&a.size), self.is_one(&b.size)) {
            (true, true) => Ok(if a.start == 0 { b.clone() } else { a.clone() }),
            (true, false) => Ok(b.clone()),
            (false, true) => Ok(a.clone()),
            (false, false) if self.same(&a.size, &b.size) && a.start == b.start => Ok(a.clone()),
            (false, false) if self.same(&a.size, &b.size) => Err(format!(
                "are misaligned along the {axis}: one covers positions {} and the other {}; \
                 `lag` shifts a range along, and `tail` and `init` drop its ends",
                self.positions(a),
                self.positions(b)
            )),
            (false, false) => Err(format!(
                "have different numbers of {axis}: {} and {}",
                self.show(&a.size),
                self.show(&b.size)
            )),
        }
    }

    /// Checks a signature against the inferred shape. Where they agree, the shape takes the
    /// sizes as the signature writes them, so that a fixed size named there (`[s, 1]`) is
    /// carried through to what is laid out below it.
    fn check_signature(&mut self, sig: &Signature, mut shape: Shape) -> Result<Shape, Diagnostic> {
        let name = &sig.name.name;
        if let Some(written) = &sig.shape {
            shape.rows.size = self.match_size(&written.rows, &shape.rows.size, name, "rows")?;
            shape.columns.size =
                self.match_size(&written.columns, &shape.columns.size, name, "columns")?;
        }
        if let Some(from) = &sig.start
            && (from.row, from.column) != (shape.rows.start, shape.columns.start)
        {
            return Err(Diagnostic::error(
                from.pos,
                format!(
                    "`{name}` starts at [{}, {}], not [{}, {}]",
                    shape.rows.start, shape.columns.start, from.row, from.column
                ),
            ));
        }
        Ok(shape)
    }

    /// An `Empty`'s shape: the size its signature gives, or one cell. Nothing works it out, so
    /// it is known when the file is written: it may name fixed sizes, but no unknown one.
    fn empty_shape(&self, sig: &Signature) -> Result<Shape, Diagnostic> {
        let mut shape = Shape::scalar();
        let Some(written) = &sig.shape else {
            return Ok(shape);
        };
        let name = &sig.name.name;
        for (extent, syntax) in [
            (&mut shape.rows, &written.rows),
            (&mut shape.columns, &written.columns),
        ] {
            let size = self.size_of_syntax(syntax)?;
            match self.normal(&size).as_constant() {
                Some(count) if count >= 1 => extent.size = size,
                Some(_) => {
                    return Err(Diagnostic::error(
                        written.pos,
                        format!("`{name}` spans at least one row and one column"),
                    ));
                }
                None => {
                    return Err(Diagnostic::error(
                        written.pos,
                        format!(
                            "`{name}` is an `Empty`, whose size is known when the file is \
                             written, so it names no unknown size"
                        ),
                    ));
                }
            }
        }
        Ok(shape)
    }

    fn size_of_syntax(&self, syntax: &SizeSyntax) -> Result<SizeExpr, Diagnostic> {
        let mut terms = Vec::new();
        for (coefficient, ident) in &syntax.terms {
            match self.model.symbols.get(&ident.name) {
                Some(Symbol::Size(var)) => terms.push((*var, *coefficient)),
                Some(Symbol::Binding(_)) => {
                    return Err(Diagnostic::error(
                        ident.pos,
                        format!("`{}` is a binding, not a size", ident.name),
                    ));
                }
                None => {
                    return Err(Diagnostic::error(
                        ident.pos,
                        format!(
                            "unknown size `{0}`; declare it with `{0} : Size Unknown`",
                            ident.name
                        ),
                    ));
                }
            }
        }
        Ok(SizeExpr::from_parts(syntax.constant, terms))
    }

    fn match_size(
        &mut self,
        written: &SizeSyntax,
        inferred: &SizeExpr,
        name: &str,
        axis: &str,
    ) -> Result<SizeExpr, Diagnostic> {
        let declared = self.size_of_syntax(written)?;
        if self.same(&declared, inferred) {
            return Ok(declared);
        }
        if let Some((var, introduced)) = self.naming(&declared, inferred) {
            self.vars[introduced].alias = Some(var);
            self.vars[var].named = Some(introduced);
            return Ok(declared);
        }
        if let ([(var, 1)], [(introduced, 1)]) = (
            declared.terms().collect::<Vec<_>>().as_slice(),
            inferred
                .rename(|var| self.canonical(var))
                .terms()
                .collect::<Vec<_>>()
                .as_slice(),
        ) && self.vars[*var].declared
            && !self.vars[*introduced].declared
            && self.vars[*var].named.is_none()
        {
            let says = |state: &VarState| match &state.bound {
                Some(bounded) => format!("`{}`", Self::bound_text(bounded)),
                None => "`Size Unknown`".to_string(),
            };
            let (declared_var, introduced_var) = (&self.vars[*var], &self.vars[*introduced]);
            if declared_var.bound.is_some() || introduced_var.bound.is_some() {
                return Err(Diagnostic::error(
                    written.pos,
                    format!(
                        "`{}` is declared {} at {}, and the size made at {} is {}",
                        self.var_name(*var),
                        says(declared_var),
                        declared_var.pos,
                        introduced_var.pos,
                        says(introduced_var)
                    ),
                ));
            }
        }
        let taken = declared
            .terms()
            .find_map(|(var, _)| self.vars[var].named.map(|introduced| (var, introduced)));
        if let Some((var, introduced)) = taken
            && inferred.any_var(|v| !self.vars[self.canonical(v)].declared)
        {
            return Err(Diagnostic::error(
                written.pos,
                format!(
                    "`{}` already names the size introduced at {}; every `..` introduces a new \
                     size, so make `{name}` from the range that has that size",
                    self.var_name(var),
                    self.vars[introduced].pos
                ),
            ));
        }
        Err(Diagnostic::error(
            written.pos,
            format!(
                "`{name}` has {} {axis}, not {}",
                self.show(inferred),
                self.show(&declared)
            ),
        ))
    }

    /// When a signature names a size that a `..` introduced, the declared variable and the
    /// introduced one: both stand alone, with coefficient 1 and the same constant beside them.
    fn naming(&self, declared: &SizeExpr, inferred: &SizeExpr) -> Option<(usize, usize)> {
        let inferred = inferred.rename(|var| self.canonical(var));
        let declared_terms: Vec<(usize, i64)> = declared.terms().collect();
        let inferred_terms: Vec<(usize, i64)> = inferred.terms().collect();
        let (&[(var, 1)], &[(introduced, 1)]) =
            (declared_terms.as_slice(), inferred_terms.as_slice())
        else {
            return None;
        };
        let declared_var = &self.vars[var];
        let bounds_agree = match (&declared_var.bound, &self.vars[introduced].bound) {
            (None, None) => true,
            (Some(a), Some(b)) => a.bound == b.bound && self.same(&a.of, &b.of),
            _ => false,
        };
        let ok = declared_var.declared
            && declared_var.kind == VarKind::Unknown
            && declared_var.named.is_none()
            && !self.vars[introduced].declared
            && bounds_agree
            && declared.constant_part() == inferred.constant_part();
        ok.then_some((var, introduced))
    }

    /// Works out what a binding's cells hold, and checks it against its signature.
    fn cell_type(&mut self, binding: usize) {
        // A binding whose shape is wrong has been reported already.
        if self.shapes[binding].is_none() {
            self.failed.insert(binding);
            return;
        }
        let b = &self.model.bindings[binding];
        let expr = self.model.definition(self.program, binding);
        let ty = match b.role {
            Role::Lambda => {
                let params: BTreeMap<String, Ty> = b
                    .params
                    .iter()
                    .cloned()
                    .zip(b.function.iter().map(|&ty| Ty::Known(ty)))
                    .collect();
                let result = *b.function.last().expect("a lambda's type has a result");
                match self.ty(expr, &params) {
                    Ok(Ty::Known(found)) if found != result => self.errors.push(Diagnostic::error(
                        expr.pos,
                        format!(
                            "`{}` gives {}, and its type says {}",
                            b.name,
                            found.word(),
                            result.word()
                        ),
                    )),
                    Ok(_) => {}
                    Err(error) => self.errors.push(error),
                }
                return;
            }
            _ => match self.ty(expr, &BTreeMap::new()) {
                Ok(ty) => ty,
                Err(error) => {
                    self.errors.push(error);
                    self.failed.insert(binding);
                    return;
                }
            },
        };
        let sig = self.model.signature(self.program, binding);
        let table = match self.model.tables.get(&binding) {
            Some(read) => self.read_table(binding, read),
            None => self.table(expr, &BTreeMap::new()),
        };
        match (table, &sig.ty) {
            (Some(columns), Some((Type::Table(declared), pos))) => {
                let agree = columns.len() == declared.len()
                    && columns.iter().zip(declared).all(|(column, (name, ty))| {
                        column.name == name.name && column.ty.is_none_or(|found| found == *ty)
                    });
                if !agree {
                    self.errors.push(Diagnostic::error(
                        *pos,
                        format!(
                            "`{}` has the columns `{}`, and its signature says `{}`",
                            b.name,
                            show_columns(&columns),
                            type_text(&Type::Table(declared.clone()))
                        ),
                    ));
                    return;
                }
                self.tables[binding] = Some(
                    declared
                        .iter()
                        .map(|(name, ty)| TableColumn {
                            name: name.name.clone(),
                            ty: Some(*ty),
                        })
                        .collect(),
                );
                return;
            }
            (Some(columns), Some((_, pos))) => {
                self.errors.push(Diagnostic::error(
                    *pos,
                    format!(
                        "`{}` is a table, so its type is written with its columns: `{}`",
                        b.name,
                        show_columns(&columns)
                    ),
                ));
                return;
            }
            (Some(columns), None) => {
                self.tables[binding] = Some(columns);
                return;
            }
            (None, Some((Type::Table(_), pos))) => {
                self.errors.push(Diagnostic::error(
                    *pos,
                    format!("`{}` is not a table, so its type is not a table's", b.name),
                ));
                return;
            }
            (None, _) => {}
        }
        if let (Some(declared), Ty::Known(found), Some((_, pos))) = (b.declared, ty, &sig.ty)
            && declared != found
        {
            self.errors.push(Diagnostic::error(
                *pos,
                format!(
                    "`{}` holds {}, not {}",
                    b.name,
                    found.word(),
                    declared.word()
                ),
            ));
        }
        self.types[binding] = b.declared.or(match ty {
            Ty::Known(found) => Some(found),
            _ => None,
        });
    }

    /// What an expression's cells hold. A lambda's parameters have the types its signature
    /// gives them.
    fn ty(&mut self, expr: &Expr, params: &BTreeMap<String, Ty>) -> Result<Ty, Diagnostic> {
        let number = |ty: Ty, what: &str| -> Result<(), Diagnostic> {
            match ty {
                Ty::Known(CellType::Text) => Err(Diagnostic::error(
                    expr.pos,
                    format!("{what} works on numbers, and this is Text; text is joined with `++`"),
                )),
                _ => Ok(()),
            }
        };
        Ok(match &expr.kind {
            ExprKind::Number { .. } => Ty::Known(CellType::Number),
            ExprKind::Str(_) => Ty::Known(CellType::Text),
            ExprKind::Bool(_) => Ty::Known(CellType::Logical),
            ExprKind::Text(_)
            | ExprKind::Constructor(_)
            | ExprKind::Update { .. }
            | ExprKind::Lambda { .. } => Ty::Any,
            ExprKind::Record(fields) => {
                for field in fields {
                    self.ty(&field.value, params)?;
                }
                Ty::Any
            }
            ExprKind::Column { table, name } => {
                let Some(columns) = self.table(table, params) else {
                    // A table that is wrong has been reported already.
                    if let ExprKind::Name(table) = &table.kind
                        && let Some(Symbol::Binding(binding)) = self.model.symbols.get(table)
                        && self.failed.contains(binding)
                    {
                        return Ok(Ty::Any);
                    }
                    let message = match &table.kind {
                        ExprKind::Name(table) => {
                            format!(
                                "`{table}` is not a table, so it has no column `{}`",
                                name.name
                            )
                        }
                        _ => format!("this is not a table, so it has no column `{}`", name.name),
                    };
                    return Err(Diagnostic::error(name.pos, message));
                };
                let Some(index) = columns.iter().position(|column| column.name == name.name) else {
                    let names: Vec<&str> =
                        columns.iter().map(|column| column.name.as_str()).collect();
                    return Err(Diagnostic::error(
                        name.pos,
                        format!(
                            "{} has no column `{}`; its columns are {}",
                            match &table.kind {
                                ExprKind::Name(row) if params.contains_key(row) => {
                                    format!("`{row}`, a row of a table,")
                                }
                                ExprKind::Name(table) => format!("the table `{table}`"),
                                _ => "the table".to_string(),
                            },
                            name.name,
                            names.join(", ")
                        ),
                    ));
                };
                self.columns.insert(expr.pos, index);
                columns[index].ty.map_or(Ty::Any, Ty::Known)
            }
            ExprKind::Name(name) => match params.get(name) {
                Some(ty) => *ty,
                None => match self.model.symbols.get(name) {
                    Some(Symbol::Binding(binding)) => {
                        self.types[*binding].map_or(Ty::Any, Ty::Known)
                    }
                    Some(Symbol::Size(_)) => Ty::Known(CellType::Number),
                    // A function of no arguments: `na`, an error, which goes with anything.
                    None => Ty::Never,
                },
            },
            ExprKind::Neg(operand) => {
                number(self.ty(operand, params)?, "negation")?;
                Ty::Known(CellType::Number)
            }
            ExprKind::Binary { op, left, right } => {
                let (left, right) = (self.ty(left, params)?, self.ty(right, params)?);
                match op {
                    BinOp::Add | BinOp::Sub | BinOp::Mul | BinOp::Div | BinOp::Pow => {
                        let what = format!("`{}`", op.symbol());
                        number(left, &what)?;
                        number(right, &what)?;
                        Ty::Known(CellType::Number)
                    }
                    BinOp::Concat => Ty::Known(CellType::Text),
                    _ => Ty::Known(CellType::Logical),
                }
            }
            ExprKind::If { cond, then, other } => {
                self.ty(cond, params)?;
                join(self.ty(then, params)?, self.ty(other, params)?)
            }
            ExprKind::List(rows) => {
                let mut ty = Ty::Never;
                for value in rows.iter().flatten() {
                    ty = join(ty, self.ty(value, params)?);
                }
                ty
            }
            ExprKind::Range { .. } => Ty::Known(CellType::Number),
            ExprKind::Apply { func, args, .. }
                if Builtin::lookup(&func.name).is_some_and(|builtin| {
                    matches!(
                        builtin,
                        Builtin::Map
                            | Builtin::ByRow
                            | Builtin::ByColumn
                            | Builtin::Reduce
                            | Builtin::Scan
                    )
                }) =>
            {
                self.lambda_ty(&func.name, args, params)?
            }
            ExprKind::Apply {
                func, args, seed, ..
            } => {
                let mut types = Vec::with_capacity(args.len());
                for arg in args {
                    types.push(self.ty(arg, params)?);
                }
                let seed = match seed {
                    Some(seed) => self.ty(seed, params)?,
                    None => Ty::Never,
                };
                if func.name == "stack"
                    && let [first, second] = args.as_slice()
                {
                    let (a, b) = (self.table(first, params), self.table(second, params));
                    let agree = match (&a, &b) {
                        (Some(a), Some(b)) => {
                            a.len() == b.len()
                                && a.iter().zip(b).all(|(a, b)| {
                                    a.name == b.name
                                        && (a.ty.is_none() || b.ty.is_none() || a.ty == b.ty)
                                })
                        }
                        (None, None) => true,
                        _ => false,
                    };
                    if !agree {
                        let show = |table: &Option<Vec<TableColumn>>| match table {
                            Some(columns) => format!("`{}`", show_columns(columns)),
                            None => "a range without named columns".to_string(),
                        };
                        return Err(Diagnostic::error(
                            second.pos,
                            format!(
                                "`stack` takes tables with the same columns, in the same order: \
                                 {} and {}",
                                show(&a),
                                show(&b)
                            ),
                        ));
                    }
                }
                if let Some(builtin) = Builtin::lookup(&func.name) {
                    for ((arg, found), wanted) in args.iter().zip(&types).zip(builtin.takes()) {
                        if let Some(problem) = argument_problem(builtin, *wanted, *found) {
                            return Err(Diagnostic::error(arg.pos, problem));
                        }
                    }
                }
                match Builtin::lookup(&func.name) {
                    Some(builtin) => match (builtin.gives(), builtin) {
                        (Gives::Always(ty), _) => Ty::Known(ty),
                        (Gives::Like(index), _) => types[index],
                        (Gives::Join, _) => {
                            types.iter().fold(Ty::Never, |ty, &next| join(ty, next))
                        }
                        (Gives::Own, Builtin::Lag) => join(types[1], seed),
                        (Gives::Own, Builtin::Csv) => Ty::Any,
                        (Gives::Own, Builtin::Na) => Ty::Never,
                        (Gives::Own, _) => {
                            unreachable!("every function whose type is its own has a rule here")
                        }
                    },
                    None => {
                        let Some(Symbol::Binding(binding)) = self.model.symbols.get(&func.name)
                        else {
                            unreachable!("Resolve checks lambda names");
                        };
                        let lambda = &self.model.bindings[*binding];
                        for ((arg, ty), (param, wanted)) in args
                            .iter()
                            .zip(&types)
                            .zip(lambda.params.iter().zip(&lambda.function))
                        {
                            if let Ty::Known(found) = ty
                                && found != wanted
                            {
                                return Err(Diagnostic::error(
                                    arg.pos,
                                    format!(
                                        "`{}` takes {} for `{param}`, and this is {}",
                                        func.name,
                                        wanted.word(),
                                        found.word()
                                    ),
                                ));
                            }
                        }
                        Ty::Known(
                            *lambda
                                .function
                                .last()
                                .expect("a lambda's type has a result"),
                        )
                    }
                }
            }
        })
    }

    /// What `map` and the others give: what their lambda gives, and for `reduce` and `scan`
    /// what their first value holds too. A row given to `by_row`'s lambda keeps the table's
    /// columns, so `row.units` is checked against them.
    fn lambda_ty(
        &mut self,
        func: &str,
        args: &[Expr],
        params: &BTreeMap<String, Ty>,
    ) -> Result<Ty, Diagnostic> {
        let accumulates = matches!(func, "reduce" | "scan");
        let data = if accumulates { &args[2] } else { &args[1] };
        let first = if accumulates {
            Some(self.ty(&args[1], params)?)
        } else {
            None
        };
        let item = self.ty(data, params)?;
        let given: Vec<Ty> = first.into_iter().chain([item]).collect();
        let gives = match &args[0].kind {
            ExprKind::Lambda {
                params: names,
                body,
            } => {
                let mut inner = params.clone();
                for (name, ty) in names.iter().zip(&given) {
                    inner.insert(name.name.clone(), *ty);
                }
                let row = (func == "by_row")
                    .then(|| self.table(data, params))
                    .flatten();
                if let (Some(columns), [name]) = (row, names.as_slice()) {
                    self.rows.insert(name.name.clone(), columns);
                }
                let gives = self.ty(body, &inner);
                for name in names {
                    self.rows.remove(&name.name);
                }
                gives?
            }
            ExprKind::Name(lambda) => {
                let Some(&Symbol::Binding(binding)) = self.model.symbols.get(lambda) else {
                    unreachable!("Resolve checks the function's name");
                };
                let b = &self.model.bindings[binding];
                for ((param, wanted), found) in b.params.iter().zip(&b.function).zip(&given) {
                    if let Ty::Known(found) = found
                        && found != wanted
                    {
                        return Err(Diagnostic::error(
                            args[0].pos,
                            format!(
                                "`{func}` gives `{lambda}` {} for `{param}`, which takes {}",
                                found.word(),
                                wanted.word()
                            ),
                        ));
                    }
                }
                Ty::Known(*b.function.last().expect("a lambda's type has a result"))
            }
            _ => unreachable!("Resolve checks that the function is a lambda or a `Lambda`"),
        };
        Ok(match first {
            Some(first) => join(first, gives),
            None => gives,
        })
    }

    /// The columns of a table read from a file. A column of blanks only takes the type that
    /// the signature gives it.
    fn read_table(&mut self, binding: usize, read: &Table) -> Option<Vec<TableColumn>> {
        let sig = self.model.signature(self.program, binding);
        let declared = match &sig.ty {
            Some((Type::Table(declared), _)) => declared.as_slice(),
            _ => &[],
        };
        let mut columns = Vec::with_capacity(read.header.len());
        for (index, name) in read.header.iter().enumerate() {
            let found = read.rows.iter().find_map(|row| match &row[index] {
                Field::Empty => None,
                Field::Number(_) => Some(CellType::Number),
                Field::Bool(_) => Some(CellType::Logical),
                Field::Text(_) => Some(CellType::Text),
            });
            let ty = found.or_else(|| {
                declared
                    .iter()
                    .find(|(declared, _)| declared.name == *name)
                    .map(|&(_, ty)| ty)
            });
            if ty.is_none() {
                self.errors.push(Diagnostic::error(
                    sig.name.pos,
                    format!(
                        "the column `{name}` of `{}` holds only blanks, so its signature gives \
                         its type, as in `{{ {name} : Number }}`",
                        sig.name.name
                    ),
                ));
                return None;
            }
            columns.push(TableColumn {
                name: name.clone(),
                ty,
            });
        }
        Some(columns)
    }

    /// The columns of an expression that is a table, if it is one.
    fn table(&mut self, expr: &Expr, params: &BTreeMap<String, Ty>) -> Option<Vec<TableColumn>> {
        match &expr.kind {
            ExprKind::Apply { func, args, .. } => match (func.name.as_str(), args.as_slice()) {
                ("filter" | "sort_by" | "sort_by_descending", [_, table])
                | ("unique", [table])
                | ("stack", [table, _]) => self.table(table, params),
                _ => None,
            },
            ExprKind::Record(fields) => Some(
                fields
                    .iter()
                    .map(|field| TableColumn {
                        name: field.name.name.clone(),
                        ty: match self.ty(&field.value, params) {
                            Ok(Ty::Known(ty)) => Some(ty),
                            _ => None,
                        },
                    })
                    .collect(),
            ),
            ExprKind::Name(name) if params.contains_key(name) => self.rows.get(name).cloned(),
            ExprKind::Name(name) => match self.model.symbols.get(name) {
                Some(Symbol::Binding(binding)) => self.tables[*binding].clone(),
                _ => None,
            },
            _ => None,
        }
    }

    fn finish(mut self) -> Result<Typed, Vec<Diagnostic>> {
        if self.errors.is_empty() {
            for var in &self.vars {
                if var.declared && var.kind == VarKind::Unknown && var.named.is_none() {
                    let name = var.name.as_deref().unwrap_or_default();
                    let made = if var.bound.is_some() {
                        "made by `filter`, such as `big : Formula size [big_n, 2] …`".to_string()
                    } else {
                        format!(
                            "made with `..`, such as `year : Formula Number size [1, {name}] …`"
                        )
                    };
                    self.errors.push(Diagnostic::error(
                        var.pos,
                        format!(
                            "nothing has the size `{name}`: name it in the signature of a range \
                             {made}"
                        ),
                    ));
                }
            }
            // A size the data decides is declared wherever a binding's cells have it.
            let mut unnamed_sizes = Vec::new();
            for (binding, shape) in self.shapes.iter().enumerate() {
                let Some(shape) = shape else { continue };
                if !self.model.bindings[binding].role.is_placed() {
                    continue;
                }
                for size in [&shape.rows.size, &shape.columns.size] {
                    let unnamed = size
                        .terms()
                        .map(|(var, _)| self.canonical(var))
                        .find(|&var| !self.vars[var].declared && self.vars[var].bound.is_some());
                    if let Some(var) = unnamed {
                        let bounded = self.vars[var].bound.clone().expect("found above");
                        let name = &self.model.bindings[binding].name;
                        let message = format!(
                            "the cells of `{name}` have the size made here, which a declaration \
                             names: add `{name}_n : {}` and name it in `{name}`'s signature",
                            Self::bound_text(&bounded)
                        );
                        let error = Diagnostic::error(self.vars[var].pos, message);
                        if !unnamed_sizes.contains(&error) {
                            unnamed_sizes.push(error);
                        }
                    }
                }
            }
            self.errors.extend(unnamed_sizes);
        }
        if !self.errors.is_empty() {
            return Err(self.errors);
        }

        // A size that no signature named takes its name from the binding that introduced it,
        // differing from every other name in more than case.
        let mut taken: BTreeSet<String> = self
            .model
            .symbols
            .keys()
            .map(|name| name.to_ascii_lowercase())
            .collect();
        for var in &mut self.vars {
            if var.name.is_some() || var.alias.is_some() {
                continue;
            }
            let binding = var.binding.expect("an introduced size has a binding");
            let base = format!("{}_n", self.model.bindings[binding].name);
            let mut name = base.clone();
            let mut suffix = 2;
            let used = |name: &str| {
                let lower = name.to_ascii_lowercase();
                taken.contains(&lower) || Builtin::lookup(&lower).is_some()
            };
            while used(&name) {
                name = format!("{base}{suffix}");
                suffix += 1;
            }
            taken.insert(name.to_ascii_lowercase());
            var.name = Some(name);
        }

        let canonical: Vec<usize> = (0..self.vars.len()).map(|v| self.canonical(v)).collect();
        let extent = |extent: &Extent| Extent {
            start: extent.start,
            size: extent.size.rename(|var| canonical[var]),
        };
        let shapes = self
            .shapes
            .iter()
            .map(|shape| {
                let shape = shape
                    .as_ref()
                    .expect("with no errors, every binding has a shape");
                Shape {
                    rows: extent(&shape.rows),
                    columns: extent(&shape.columns),
                }
            })
            .collect();
        // A size the data decides is laid out as the most it can be, where that is known.
        let mut layout: Vec<Option<i64>> = Vec::with_capacity(self.vars.len());
        for var in &self.vars {
            let value = match (var.kind, &var.bound) {
                (VarKind::Fixed(value), _) => Some(value),
                (VarKind::Unknown, Some(bounded)) => bounded
                    .of
                    .rename(|v| canonical[v])
                    .substitute(|v| layout.get(v).copied().flatten())
                    .as_constant(),
                (VarKind::Unknown, None) => None,
            };
            layout.push(value);
        }
        let vars = self
            .vars
            .into_iter()
            .zip(layout)
            .map(|(var, layout)| Var {
                bound: match var.kind {
                    VarKind::Fixed(_) => None,
                    VarKind::Unknown => layout,
                },
                introduced_by: if var.declared || var.alias.is_some() {
                    None
                } else {
                    var.binding
                },
                // A size that stands for a declared one appears in no shape, so needs no name.
                name: var.name.unwrap_or_default(),
                kind: var.kind,
            })
            .collect();
        Ok(Typed {
            vars,
            shapes,
            types: self.types,
            tables: self.tables,
            columns: self.columns,
            along: self.along,
            drivers: self.drivers,
            carried: self.carried,
            axes: self.axes,
        })
    }
}

fn count(n: usize) -> i64 {
    i64::try_from(n).expect("a list in a file has fewer than 2^63 values")
}

/// How many positions a `lag` moves: a whole number, at least 1, written in the file.
fn steps(arg: &Expr, what: &str) -> Result<i64, Option<Diagnostic>> {
    match arg.integer() {
        Some(steps) if steps >= 1 => Ok(steps),
        _ => Err(fail(
            arg.pos,
            format!(
                "`{what}` takes a whole number of positions written in the file, such as `{what} \
                 1 revenue`"
            ),
        )),
    }
}

fn function_name(expr: &Expr) -> &str {
    match &expr.kind {
        ExprKind::Apply { func, .. } => &func.name,
        _ => "",
    }
}

/// What is wrong with giving `found` to an argument of `builtin` that takes `wanted`, if
/// anything. A number may stand where a logical goes and the other way round, as in arithmetic;
/// text goes only where text does, and only text goes there.
fn argument_problem(builtin: Builtin, wanted: Option<CellType>, found: Ty) -> Option<String> {
    let (Some(wanted), Ty::Known(found)) = (wanted, found) else {
        return None;
    };
    let fits = match wanted {
        CellType::Text => found == CellType::Text,
        CellType::Number | CellType::Logical => found != CellType::Text,
    };
    (!fits).then(|| {
        format!(
            "`{}` takes {} here, and this is {}",
            builtin.name(),
            wanted.word(),
            found.word()
        )
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::parse;
    use crate::resolve::resolve;

    /// A workbook of one sheet, `main`, for the bindings in `body`, which start on line 5.
    fn file(body: &str) -> String {
        format!(
            "workbook : Workbook\nworkbook = {{ sheets = [main] }}\nmain : Sheet\nmain = \
             {{ name = 'Main' }}\n{body}"
        )
    }

    fn typed(body: &str) -> Result<(Model, Typed), Vec<String>> {
        let program = parse(&file(body)).expect("parses");
        let model = resolve(&program, &crate::Inputs::new()).expect("resolves");
        infer(&program, &model)
            .map(|typed| (model, typed))
            .map_err(|errors| errors.iter().map(ToString::to_string).collect())
    }

    fn shapes(body: &str) -> Vec<String> {
        let (model, typed) = typed(body).expect("infers");
        model
            .bindings
            .iter()
            .zip(&typed.shapes)
            .filter(|(binding, _)| {
                matches!(binding.role, Role::Formula | Role::Value | Role::Local)
            })
            .map(|(binding, shape)| {
                format!(
                    "{} [{}, {}] start [{}, {}]",
                    binding.name,
                    typed.format(&shape.rows.size),
                    typed.format(&shape.columns.size),
                    shape.rows.start,
                    shape.columns.start
                )
            })
            .collect()
    }

    const FORECAST: &str = "horizon : Value Number at main D2\nhorizon = 5\nperiod : Local\nperiod \
                            = 1 .. horizon\nrevenue : Local\nrevenue = 100 * 1.1 ^ period\n";

    #[test]
    fn spans_follow_lag_tail_and_init() {
        let source = format!(
            "{FORECAST}growth : Local\ngrowth = tail revenue / lag 1 revenue - 1\nleading : \
             Local\nleading = init revenue\ncol : Local\ncol = tail (transpose revenue)\n"
        );
        assert_eq!(
            shapes(&source),
            [
                "horizon [1, 1] start [0, 0]",
                "period [1, period_n] start [0, 0]",
                "revenue [1, period_n] start [0, 0]",
                "growth [1, period_n - 1] start [0, 1]",
                "leading [1, period_n - 1] start [0, 0]",
                "col [period_n - 1, 1] start [1, 0]",
            ]
        );
    }

    #[test]
    fn a_range_of_rows_and_columns_says_which_way_it_lags() {
        let grid = "grid : Local\ngrid = [1, 2; 3, 4]\n";
        assert_eq!(
            typed(&format!("{grid}x : Local\nx = tail grid\n")).expect_err("no direction"),
            [
                "8:5: error: `tail` works across or down, and this range has more than one row \
                 and column: say which, as in `tail across x`"
            ]
        );
        assert_eq!(
            shapes(&format!("{grid}x : Local\nx = lag down 1 grid\n"))[1],
            "x [1, 2] start [1, 0]"
        );
    }

    #[test]
    fn misaligned_ranges_do_not_combine() {
        let source = format!("{FORECAST}growth : Local\ngrowth = tail revenue / init revenue\n");
        assert_eq!(
            typed(&source).expect_err("misaligned"),
            [
                "12:23: error: the two sides of `/` are misaligned along the columns: one covers \
                 positions [1, period_n) and the other [0, period_n - 1); `lag` shifts a range \
                 along, and `tail` and `init` drop its ends"
            ]
        );
    }

    #[test]
    fn every_range_made_with_dots_has_its_own_size() {
        let source = format!(
            "{FORECAST}other : Local\nother = 1 .. horizon\ntotal : Local\ntotal = period + \
             other\n"
        );
        assert_eq!(
            typed(&source).expect_err("two sizes"),
            [
                "14:16: error: the two sides of `+` have different numbers of columns: period_n \
                 and other_n"
            ]
        );
    }

    #[test]
    fn a_signature_names_a_size_and_is_checked() {
        let named = "n : Size Unknown\nh : Local\nh = 5\np : Local size [1, n]\np = 1 .. h\nq : Local size \
                     [1, n - 1]\nq = tail p\n";
        assert_eq!(
            shapes(named),
            [
                "h [1, 1] start [0, 0]",
                "p [1, n] start [0, 0]",
                "q [1, n - 1] start [0, 1]"
            ]
        );
        let wrong = "n : Size Unknown\nh : Local\nh = 5\np : Local size [1, n]\np = 1 .. h\nq : Local size \
                     [1, n]\nq = tail p\n";
        assert_eq!(
            typed(wrong).expect_err("wrong size"),
            ["10:20: error: `q` has n - 1 columns, not n"]
        );
    }

    #[test]
    fn fixed_sizes_compare_by_value() {
        let source = "s : Size Fixed\ns = 3\nsegment : Local size [1, 3]\nsegment = 1 .. s\nbase : \
                      Local\nbase = [100; 200; 300]\nyear : Local\nyear = 1 .. 5\ngrid : \
                      Local\ngrid = base * 1.1 ^ year\n";
        assert_eq!(
            shapes(source),
            [
                "segment [1, 3] start [0, 0]",
                "base [3, 1] start [0, 0]",
                "year [1, 5] start [0, 0]",
                "grid [3, 5] start [0, 0]",
            ]
        );
    }

    #[test]
    fn a_seed_fills_the_first_position() {
        let source = format!("{FORECAST}opening : Local\nopening = lag 1 revenue seed 100\n");
        assert_eq!(shapes(&source)[3], "opening [1, period_n] start [0, 0]");
    }

    #[test]
    fn cell_types_are_worked_out_and_checked() {
        let (model, found) = typed(
            "a : Local\na = 1 .. 3\nb : Local\nb = a > 1\nt : Local\nt = \"x\" ++ \"y\"\nd : \
             Local\nd = if b then 1 else na\n",
        )
        .expect("infers");
        let types: Vec<Option<CellType>> = model
            .bindings
            .iter()
            .zip(&found.types)
            .filter(|(binding, _)| binding.role == Role::Local)
            .map(|(_, ty)| *ty)
            .collect();
        assert_eq!(
            types,
            [
                Some(CellType::Number),
                Some(CellType::Logical),
                Some(CellType::Text),
                Some(CellType::Number)
            ]
        );
        assert_eq!(
            typed(
                "a : Local Text\na = 1\nb : Local\nb = \"x\" + 1\nf : Lambda Number -> \
                 Number\nf x = x\nt : Local\nt = f \"y\"\n"
            )
            .expect_err("wrong types"),
            [
                "5:11: error: `a` holds Number, not Text",
                "8:9: error: `+` works on numbers, and this is Text; text is joined with `++`",
                "12:7: error: `f` takes Number for `x`, and this is Text",
            ]
        );
        assert_eq!(
            typed("a : Local\na = sqrt \"x\"\nb : Local\nb = round 2 \"y\"\n")
                .expect_err("wrong types"),
            [
                "6:10: error: `sqrt` takes Number here, and this is Text",
                "8:13: error: `round` takes Number here, and this is Text",
            ]
        );
    }
}
