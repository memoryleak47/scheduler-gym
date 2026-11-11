fn sched_init() -> BackoffScheduler {
    BackoffScheduler::default()
}

fn sched_iter<'a, L: Language, N: Analysis<L>, IterData: IterationData<L, N>>(ctxt: &mut Ctxt<'a, L, N, IterData>, sched: &mut BackoffScheduler) -> Result<(), StopReason> {
    let i = ctxt.runner.iterations.len();

    let mut matches = Vec::new();

    for rw in ctxt.rws {
        let ms = sched.search_rewrite(i, &ctxt.runner.egraph, rw);
        ctxt.matches += ms.iter().map(|m| m.substs.len()).sum::<usize>();
        matches.push(ms);
        ctxt.check_limits()?;
    }

    for (rw, ms) in ctxt.rws.iter().zip(matches.into_iter()) {
        sched.apply_rewrite(i, &mut ctxt.runner.egraph, rw, ms);
        ctxt.check_limits()?;
    }

    ctxt.runner.egraph.rebuild();

    Ok(())
}
