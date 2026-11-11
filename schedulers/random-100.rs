// Ablation of source-cost-*: same match collection and application budget,
// but matches are applied in a (seeded) random order instead of by inclusion cost.

const RANDOM_SEED: u64 = 0x0012_3456_78AB_CDEF;

struct RandomSched {
    backoff: BackoffScheduler,
    rng: SplitMix64,
}

fn sched_init() -> RandomSched {
    RandomSched {
        backoff: BackoffScheduler::default(),
        rng: SplitMix64(RANDOM_SEED),
    }
}

fn sched_iter<'a, L: Language, N: Analysis<L>, IterData: IterationData<L, N>>(
    ctxt: &mut Ctxt<'a, L, N, IterData>,
    sched: &mut RandomSched,
) -> Result<(), StopReason> {
    let mut matches: Vec<(usize, Id, Subst)> = Vec::new();

    for (rw_i, rw) in ctxt.rws.iter().enumerate() {
        for m in sched
            .backoff
            .search_rewrite(ctxt.runner.iterations.len(), &ctxt.runner.egraph, rw)
        {
            ctxt.matches += m.substs.len();
            ctxt.check_limits()?;

            let lhs = m.eclass;
            for subst in m.substs {
                matches.push((rw_i, lhs, subst));

                ctxt.check_limits()?;
            }
        }
    }

    sched.rng.shuffle(&mut matches);

    let eg_data = |eg: &EGraph<_, _>| (eg.number_of_classes(), eg.total_size());

    let mut counter = 0;

    for (rw_i, lhs, subst) in &matches {
        let rw = &ctxt.rws[*rw_i];
        let pat_ast = rw.searcher.get_pattern_ast();

        let prev_data = eg_data(&ctxt.runner.egraph);
        rw.applier
            .apply_one(&mut ctxt.runner.egraph, *lhs, subst, pat_ast, rw.name);
        let post_data = eg_data(&ctxt.runner.egraph);

        if prev_data != post_data {
            counter += 1;
            if counter >= 100 {
                break;
            }
        }

        ctxt.check_limits()?;
    }

    ctxt.runner.egraph.rebuild();
    Ok(())
}

// === rng ===
// Inlined, because not every case study depends on `rand`.

struct SplitMix64(u64);

impl SplitMix64 {
    fn next_u64(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }

    // uniform in 0..n (n > 0), via Lemire's multiply-shift with rejection
    fn below(&mut self, n: u64) -> u64 {
        let threshold = n.wrapping_neg() % n;
        loop {
            let m = (self.next_u64() as u128) * (n as u128);
            if (m as u64) >= threshold {
                return (m >> 64) as u64;
            }
        }
    }

    // Fisher-Yates
    fn shuffle<T>(&mut self, v: &mut [T]) {
        for i in (1..v.len()).rev() {
            let j = self.below(i as u64 + 1) as usize;
            v.swap(i, j);
        }
    }
}
