//! Step 5, Ground: fixed sizes replaced by their values. Every anchor becomes a cell, and every
//! extent depends on unknown sizes only.
//!
//! A size that a formula's data decides, at most or as large as a size known when the file is
//! written, keeps its name in the shapes the file prints, and is that size in the extents the
//! cells are checked and laid out by: nothing past it is ever spilled into.

use std::collections::BTreeMap;

use crate::infer::{Extent, Shape, Typed};
use crate::place::Placed;
use crate::size::SizeExpr;

/// A cell, `(row, column)`, both counted from 1.
pub type Cell = (i64, i64);

/// Where everything goes, as cells, and every binding's shape in unknown sizes only.
#[derive(Clone, Debug, Default)]
pub struct Grounded {
    pub anchors: BTreeMap<usize, Cell>,
    /// Each binding's shape, as the file prints it.
    pub shapes: Vec<Shape>,
    /// Each binding's shape as its cells are laid out, with each size the data decides at its
    /// bound where that is known.
    pub extents: Vec<Shape>,
}

/// Substitutes the fixed sizes in `placed` and in the shapes of `typed`.
pub fn ground(typed: &Typed, placed: &Placed) -> Grounded {
    let size = |size: &SizeExpr| size.substitute(|var| typed.fixed(var));
    let laid_out = |size: &SizeExpr| size.substitute(|var| typed.layout(var));
    let extent = |extent: &Extent| Extent {
        start: extent.start,
        size: size(&extent.size),
    };
    let laid_out_extent = |extent: &Extent| Extent {
        start: extent.start,
        size: laid_out(&extent.size),
    };
    Grounded {
        anchors: placed
            .anchors
            .iter()
            .map(|(&binding, at)| {
                (
                    binding,
                    (
                        constant(&laid_out(&at.row)),
                        constant(&laid_out(&at.column)),
                    ),
                )
            })
            .collect(),
        shapes: typed
            .shapes
            .iter()
            .map(|shape| Shape {
                rows: extent(&shape.rows),
                columns: extent(&shape.columns),
            })
            .collect(),
        extents: typed
            .shapes
            .iter()
            .map(|shape| Shape {
                rows: laid_out_extent(&shape.rows),
                columns: laid_out_extent(&shape.columns),
            })
            .collect(),
    }
}

fn constant(size: &SizeExpr) -> i64 {
    size.as_constant()
        .expect("Place keeps unknown sizes out of anchors")
}
