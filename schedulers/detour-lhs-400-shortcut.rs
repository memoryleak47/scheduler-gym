// The e-match shortcut makes stuff worse. I am unsure why. It seems to indicate a bug.

fn sched_init() -> Stats { Stats::default() }

const MAX: usize = 400;

fn sched_iter<'a, L: Language, N: Analysis<L>, IterData: IterationData<L, N>>(ctxt: &mut Ctxt<'a, L, N, IterData>, stats: &mut Stats) -> Result<(), StopReason> {
    let ex = Extractor::new(&ctxt.runner.egraph, AdditiveCostFn(ctxt.cfg.cf));
    let ctxt_cost = compute_ctxt_costs(&ex, ctxt);

    let mut classes: Vec<(Id, /*detour cost*/ Cost)> = ctxt.runner.egraph.classes()
            .map(|x| x.id)
            .map(|id| (id, ctxt_cost.get(&id).unwrap_or(&ctxt.cfg.unreachable_cost) + ex.find_best_cost(id)))
            .collect();
    classes.sort_by_key(|x| x.1);

    // has to remain sorted by Cost, and length <= MAX.
    let mut matches: Vec<(Cost, usize, Id, Subst)> = Vec::new();

    let mut special_matches: Vec<(Cost, usize, Id, Subst)> = Vec::new();

    let mut rws: Box<[(usize, _)]> = ctxt.rws.iter().enumerate().collect();

    // We'll start with the non-special ones, and then come the special ones.
    rws.sort_by_key(|(_, rw)| ctxt.special_rules.contains(&rw.name));

    for (rw_i, rw) in rws {
        let effective_pat = rw.searcher.get_pattern_ast().unwrap();
        let is_special = ctxt.special_rules.contains(&rw.name);

        let mmatches = ematch(ctxt, rw, stats, &matches, &classes)?;

        for m in mmatches {
            let lhs = m.eclass;
            for subst in m.substs {
                if let Some(rhs_eclass) = rw.applier.get_pattern_ast().and_then(|rhs_pat| lookup_pat(rhs_pat, &ctxt.runner.egraph, &subst)) {
                    if lhs == rhs_eclass {
                        continue
                    }
                }

                let pat_cost = pat_cost(effective_pat, &subst, &ex, ctxt.cfg.cf);
                let cx_cost = *ctxt_cost.get(&lhs).unwrap_or(&ctxt.cfg.unreachable_cost); // this is the cost you get from not being able to reach any root.
                let detour_cost = cx_cost + pat_cost;

                let rmatches = if is_special { &mut special_matches } else { &mut matches };
                sorted_push_by_key(rmatches, (detour_cost, rw_i, lhs, subst), |x| x.0);
                while matches.len() > MAX { matches.pop(); }
                if let Some((threshold, ..)) = matches.last() {
                    while let Some((cost, ..)) = special_matches.last() {
                        if cost > threshold { special_matches.pop(); }
                        else { break }
                    }
                }

                ctxt.check_limits()?;
            }
        }
    }

    let eg_data = |eg: &EGraph<_, _>| (eg.number_of_classes(), eg.total_size());

    matches.extend(special_matches);
    matches.sort_by_key(|x| x.0);

    let mut counter = 0;
    for (c, rw_i, lhs, subst) in matches {
        let rw = &ctxt.rws[rw_i];
        let pat_ast = rw.searcher.get_pattern_ast();

        let predata = eg_data(&ctxt.runner.egraph);
        rw.applier.apply_one(&mut ctxt.runner.egraph, lhs, &subst, pat_ast, rw.name);
        let postdata = eg_data(&ctxt.runner.egraph);
            
        if predata != postdata {
            counter += 1;
            if counter >= MAX {
                break
            }
        }

        ctxt.check_limits()?;
    }

    ctxt.runner.egraph.rebuild();
    Ok(())
}


fn ematch<'a, 'r, L: Language, N: Analysis<L>, IterData: IterationData<L, N>>(ctxt: &Ctxt<'a, L, N, IterData>, rw: &'r Rewrite<L, N>, stats: &mut Stats, matches: &[(Cost, usize, Id, Subst)], classes: &[(Id, Cost)]) -> Result<Vec<SearchMatches<'r, L>>, StopReason> {
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

    let mut mmatches = Vec::new();

    let mut total_len = 0;
    for (id, detour) in classes {
        if matches.len() == MAX && matches.last().unwrap().0 <= *detour { break }

        let local_threshold = (threshold - mmatches.len()).saturating_add(1);
        let submatches = rw.searcher.search_eclass_with_limit(&ctxt.runner.egraph, *id, local_threshold);
        if let Some(m) = &submatches {
            total_len += m.substs.len();
        }

        if total_len > threshold {
            let ban_length = stats.ban_length << stats.times_banned;
            stats.times_banned += 1;
            stats.banned_until = iteration + ban_length;
            return Ok(Vec::new())
        }

        mmatches.extend(submatches);

        ctxt.check_limits()?;
    }

    Ok(mmatches)
}

fn sorted_push_by_key<T, K: Ord, F: FnMut(&T) -> K>(vec: &mut Vec<T>, item: T, mut key_fn: F) {
    let key = key_fn(&item);
    let index = match vec.binary_search_by_key(&key, key_fn) {
        Ok(i) | Err(i) => i,
    };
    vec.insert(index, item);
}

// === ctxt cost ===

fn compute_ctxt_costs<'a, L: Language, N: Analysis<L>, IterData: IterationData<L, N>>(ex: &Extractor<AdditiveCostFn<L>, L, N>, ctxt: &Ctxt<'a, L, N, IterData>) -> HashMap<Id, Cost> {
    let mut ctxt_cost = HashMap::new();

    let mut queue: MinPrioQueue<Cost, Id> = MinPrioQueue::new();

    // initial
    for root in &ctxt.runner.roots {
        queue.push(0, *root);
    }

    while let Some((cst, i)) = queue.pop() {
        if ctxt_cost.contains_key(&i) { continue }
        ctxt_cost.insert(i, cst);
        for e in &ctxt.runner.egraph[i].nodes {
            let e_cost = AdditiveCostFn(ctxt.cfg.cf).cost(e, |k| ex.find_best_cost(k));
            for &c in e.children() {
                // optimization: don't push junk to the queue.
                // NOTE: if we remembered what's the best thing we already pushed to the queue for some class,
                // we could do more efficient pruning.
                if ctxt_cost.contains_key(&c) { continue }

                let c_cost = ex.find_best_cost(c);
                let ncst = e_cost + cst - c_cost;
                queue.push(ncst, c);
            }
        }
    }

    ctxt_cost
}

fn pat_cost<L: Language, N: Analysis<L>>(pat: &PatternAst<L>, subst: &Subst, ex: &Extractor<AdditiveCostFn<L>, L, N>, cf: fn(&L) -> Cost) -> Cost {
    let mut vec: Vec<Cost> = Vec::new();
    for i in 0..pat.as_ref().len() {
        let cost = match &pat[i.into()] {
            ENodeOrVar::ENode(n) => AdditiveCostFn(cf).cost(n, |x| vec[usize::from(x)]),
            ENodeOrVar::Var(v) => ex.find_best_cost(subst[*v]),
        };
        vec.push(cost);
    }
    vec.last().copied().unwrap()
}

fn lookup_pat<L: Language, N: Analysis<L>>(pat: &PatternAst<L>, eg: &EGraph<L, N>, subst: &Subst) -> Option<Id> {
    let mut vec = Vec::new();
    for i in 0..pat.as_ref().len() {
        match &pat[i.into()] {
            ENodeOrVar::ENode(n) => {
                let mut n = n.clone().map_children(|k| vec[usize::from(k)]);
                let k = eg.lookup(&mut n)?;
                vec.push(k);
            },
            ENodeOrVar::Var(v) => vec.push(subst[*v]),
        }
    }
    vec.last().copied()
}

// === minqueue ===

use std::cmp::Ordering;
use std::collections::{BinaryHeap, HashMap, BTreeMap};

struct MinPrioQueue<U, T>(BinaryHeap<WithOrdRev<U, T>>);

impl<U: Ord, T: Eq> MinPrioQueue<U, T> {
    pub fn new() -> Self {
        MinPrioQueue(BinaryHeap::default())
    }

    pub fn push(&mut self, u: U, t: T) {
        self.0.push(WithOrdRev(u, t));
    }

    pub fn pop(&mut self) -> Option<(U, T)> {
        self.0.pop().map(|WithOrdRev(u, t)| (u, t))
    }
}

// Takes the `Ord` from U, but reverses it.
#[derive(PartialEq, Eq, Debug)]
struct WithOrdRev<U, T>(pub U, pub T);

impl<U: Ord, T: Eq> PartialOrd for WithOrdRev<U, T> {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        // It's the other way around, because we want a min-heap!
        other.0.partial_cmp(&self.0)
    }
}
impl<U: Ord, T: Eq> Ord for WithOrdRev<U, T> {
    fn cmp(&self, other: &Self) -> Ordering {
        self.partial_cmp(&other).unwrap()
    }
}

// backoff scheduler.

struct RuleStats {
    banned_until: usize,
    times_banned: usize,
    match_limit: usize,
    ban_length: usize,
}

type Stats = HashMap<Symbol, RuleStats>;
