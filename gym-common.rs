use std::time::Duration;
use egg::*;

pub type Cost = u128;

pub struct Limits {
    pub node_limit: usize,
    pub time_limit: Duration,
}

pub struct CostConfig<L> {
    pub cf: fn(&L) -> Cost,
    pub offset: Cost,
    pub unreachable_cost: Cost,
}

pub struct AdditiveCostFn<L: Language>(pub fn(&L) -> Cost);

impl<L: Language> CostFunction<L> for AdditiveCostFn<L> {
    type Cost = Cost;

    fn cost<C>(&mut self, enode: &L, costs: C) -> Self::Cost where C: FnMut(Id) -> Self::Cost {
        enode.children().iter().copied().map(costs).fold(self.0(enode), |x, y| x+y)
    }
}
