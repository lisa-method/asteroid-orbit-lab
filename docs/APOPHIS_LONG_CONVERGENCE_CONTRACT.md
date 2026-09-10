# Дополнительная проверка длинной интеграции — 2026-09-09

Post-hoc follow-up после шести frozen encounter-closure runs. Annual extrap
coarse/fine0.270m, fine/RK40.342m, fine/DPultra0.885m; long coarse/fine1.518m
не проходят прежний1m gate. Annual teacher error2.552km, local restart3.328km:
одна ошибка initial state до day99 не объясняет residual.

До новых результатов фиксируются ДВА контроля с тем же covariance mean2021,
exact SBDB NG, extended native DE441, силой и895 узлами:

- modified-midpoint extrap10/8: max step .125d, rtol1e-16,
  atol position/velocity1e-19/1e-20;
- независимый DP5(4): max step .03125d, rtol1e-16,
  atol position/velocity1e-18/1e-19.

Hard boundaries integer days сохраняются в обеих ветвях. Это refinement,
не изменение старой frozen серии. Сравниваем обе новые ветви и новую extrap
со старой fine; порог<=1m неизменен. Полный ансамбль covariance не проверен
этими nominal runs, интервалы остаются formal/unvalidated. Нет подгонки
initial states, NG, сил и model-selector thresholds по этим значениям.
