// A version of the backoff scheduler that doesn't completely ignore time limits.
// I want to double-check that this behaves the same as the original backoff.rs.

use std::collections::HashMap;

fn sched_init() -> Stats { Stats::default()  }

fn sched_iter<'a, L: Language, N: Analysis<L>, IterData: IterationData<L, N>>(ctxt: &mut Ctxt<'a, L, N, IterData>, stats: &mut Stats) -> Result<(), StopReason> {
    let mut matches = Vec::new();

    for rw in ctxt.rws {
        matches.push(search_backoff_rewrite(stats, ctxt, rw)?);
        ctxt.check_limits()?;
    }

    for (rw, ms) in ctxt.rws.iter().zip(matches.into_iter()) {
        rw.apply(&mut ctxt.runner.egraph, &ms);
        ctxt.check_limits()?;
    }

    ctxt.runner.egraph.rebuild();
    Ok(())
}

fn search_backoff_rewrite<'a, L: Language, N: Analysis<L>, IterData: IterationData<L, N>>(stats: &mut Stats, ctxt: &Ctxt<'_, L, N, IterData>, rw: &'a Rewrite<L, N>) -> Result<Vec<SearchMatches<'a, L>>, StopReason> {
    let iteration = ctxt.runner.iterations.len();
    let egraph = &ctxt.runner.egraph;
    let stats = stats.entry(rw.name).or_insert(RuleStats {
        banned_until: 0,
        times_banned: 0,
        match_limit: 1000,
        ban_length: 5,
    });

    if iteration < stats.banned_until { return Ok(Vec::new()) }

    let threshold = stats
        .match_limit
        .checked_shl(stats.times_banned as u32)
        .unwrap();

    let mut matches = Vec::new();

    let mut total_len = 0;
    for c in ctxt.runner.egraph.classes().map(|x| x.id) {
        let local_threshold = (threshold - matches.len()).saturating_add(1);
        let submatches = rw.searcher.search_eclass_with_limit(&ctxt.runner.egraph, c, local_threshold);
        total_len += submatches.iter().map(|m| m.substs.len()).sum::<usize>();

        if total_len > threshold {
            let ban_length = stats.ban_length << stats.times_banned;
            stats.times_banned += 1;
            stats.banned_until = iteration + ban_length;
            return Ok(Vec::new())
        }

        matches.extend(submatches);
    }

    Ok(matches)
}

struct RuleStats {
    banned_until: usize,
    times_banned: usize,
    match_limit: usize,
    ban_length: usize,
}

type Stats = HashMap<Symbol, RuleStats>;
