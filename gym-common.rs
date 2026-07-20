use std::time::{Duration, Instant};
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

struct Ctxt<'a, L: Language, N: Analysis<L>, IterData: IterationData<L, N>> {
    runner: Runner<L, N, IterData>,
    rws: &'a [Rewrite<L, N>],
    limits: Limits,
    cfg: CostConfig<L>,

    start: Instant,
}

fn mk_iteration<'a, L: Language, N: Analysis<L> + Default, IterData: IterationData<L, N>>(ctxt: &mut Ctxt<'a, L, N, IterData>, stop_reason: Option<StopReason>, it_start: Instant) -> Iteration<IterData> {
    let eg = std::mem::take(&mut ctxt.runner.egraph);
    let mut mock_runner = Runner::new(N::default()).with_egraph(eg);
    mock_runner.roots = ctxt.runner.roots.clone();
    mock_runner = mock_runner.run([]);
    let mut it = mock_runner.iterations.pop().unwrap();
    ctxt.runner.egraph = mock_runner.egraph;

    // it.egraph_nodes is set correctly by mock runner
    // it.egraph_classes is  set correctly by mock runner
    it.applied = Default::default(); // set to default
    it.hook_time = 0.0; // set to default
    it.search_time = 0.0; // set to default
    it.apply_time = 0.0; // set to default
    it.rebuild_time = 0.0; // set to default
    it.total_time = it_start.elapsed().as_secs_f64(); // Note that this iteration counting counts *everything* in an iteration. This is different from egg, which excludes hooks etc.
    // it.data is set correctly by mock runner
    it.n_rebuilds = 0; // set to default
    it.stop_reason = stop_reason.clone();

    if stop_reason.is_some() {
        ctxt.runner.stop_reason = stop_reason;
    }

    it
}

fn dump_iteration<'a, L: Language, N: Analysis<L> + Default, IterData: IterationData<L, N>>(ctxt: &Ctxt<'a, L, N, IterData>) {
    let ex = Extractor::new(&ctxt.runner.egraph, AdditiveCostFn(ctxt.cfg.cf));
    let costs = ctxt.runner.roots.iter().map(|x| ex.find_best_cost(*x)).collect::<Box<[_]>>();
    let total_size = ctxt.runner.egraph.total_size();
    let time = ctxt.start.elapsed().as_secs_f64();
    let it = ctxt.runner.iterations.len();
    let stop = &ctxt.runner.stop_reason;

    use std::fs::OpenOptions;
    use std::io::Write;

    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .open("entries.txt")
        .unwrap();
    writeln!(file, "#ENTRY: costs={costs:?}, total_size={total_size}, time={time}, iteration={it}, stop={stop:?}").unwrap();
}

impl<'a, L: Language, N: Analysis<L>, IterData: IterationData<L, N>> Ctxt<'a, L, N, IterData> {
    fn check_limits(&self) -> Result<(), StopReason> {
        let elapsed = self.start.elapsed();
        if elapsed > self.limits.time_limit { return Err(StopReason::TimeLimit(elapsed.as_secs_f64())) }

        let size = self.runner.egraph.total_size();
        if size > self.limits.node_limit { return Err(StopReason::NodeLimit(size)) }

        Ok(())
    }
}

fn call_hooks<'a, L: Language, N: Analysis<L>, IterData: IterationData<L, N>>(ctxt: &mut Ctxt<'a, L, N, IterData>) -> Result<(), StopReason> {
    let mut hooks = std::mem::take(&mut ctxt.runner.hooks);
    let res = hooks.iter_mut().try_for_each(|hook| hook(&mut ctxt.runner).map_err(StopReason::Other));
    ctxt.runner.hooks = hooks;
    res
}

#[allow(unused)]
pub fn run<L: Language, N: Analysis<L> + Default, IterData: IterationData<L, N>>(runner: Runner<L, N, IterData>, rws: &[Rewrite<L, N>], limits: Limits, cfg: CostConfig<L>) -> Runner<L, N, IterData> {
    let mut ctxt = Ctxt {
        runner,
        rws,
        limits,
        cfg,

        start: Instant::now(),
    };
    let mut state = sched_init();

    // The initial e-graph might be dirty.
    ctxt.runner.egraph.rebuild();

    while ctxt.runner.stop_reason.is_none() {
        let mut body = || {
            ctxt.check_limits()?;

            call_hooks(&mut ctxt)?;

            ctxt.check_limits()?;

            sched_iter(&mut ctxt, &mut state)?;

            ctxt.check_limits()?;

            Ok(())
        };

        let it_start = Instant::now();
        let result = body();

        let it = mk_iteration(&mut ctxt, result.err(), it_start);
        ctxt.runner.iterations.push(it);

        dump_iteration(&ctxt);
    }

    ctxt.runner
}

