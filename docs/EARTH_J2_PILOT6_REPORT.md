# Earth J2 audit — pilot6

> Generated 2026-09-05T13:11:16.340418+00:00. This is a force-model and interpolation audit on the engineering pilot.

## Scope and conventions

- Base force is solar GR + the 16 SB441-N16 massive asteroid perturbers plus the nine configured planetary perturbers.
- Both no-NG and nominal Horizons NG matching branches are evaluated; NG is not a primary selector feature.
- Daily-only interpolation uses the daily body rows. Merged interpolation replaces only Earth/Moon rows inside the stored 36-hour refinement and uses daily rows outside it.
- Earth J2 uses the supplied IERS 2010 J2 and equatorial reference radius with either a fixed J2000 pole or the approximate IAU mean-pole model.
- Closest-approach values are sampled grid minima, not continuous optimization or an operational hazard product.

## Constants and provenance

- J2: `0.00108262545`; reference radius: `6378.1366 km` (`4.26352097804e-05 AU`).
- Pole source: [NAIF text PCK](https://naif.jpl.nasa.gov/pub/naif/toolkit_docs/C/req/pck.html); this is an approximate IAU mean pole in TDB, without nutation/EOP or high-precision ITRF orientation.
- Radius source: [https://ssd.jpl.nasa.gov/planets/phys_par.html](https://ssd.jpl.nasa.gov/planets/phys_par.html).
- Reference states are stored Horizons model-derived ephemerides; results should be read as propagation/model matching diagnostics.

## apophis_earth_2029_refined36h

Grid: 433 samples from 2029-04-13T00:00:00 to 2029-04-14T12:00:00 TDB; reference grid minimum is 38014.448 km at 2029-04-13T21:45:00.

| Variant | Max position error, km | Endpoint error, km | Grid min, km | Runtime, s | RK4 steps |
| --- | ---: | ---: | ---: | ---: | ---: |
| baseGR+SB16:daily | 3.66812 | 3.66812 | 38014.6 | 2.800 | 3,592 |
| baseGR+SB16:merged | 3.65057 | 3.65057 | 38014.5 | 2.793 | 3,592 |
| baseGR+SB16:merged+J2fixed_j2000 | 0.00942613 | 0.0094014 | 38014.4 | 2.976 | 3,592 |
| baseGR+SB16:merged+J2iau | 0.00184481 | 0.000768364 | 38014.4 | 2.968 | 3,592 |
| baseGR+SB16+NG:daily | 3.66815 | 3.66815 | 38014.6 | 2.875 | 3,592 |
| baseGR+SB16+NG:merged | 3.65059 | 3.65059 | 38014.5 | 2.862 | 3,592 |
| baseGR+SB16+NG:merged+J2fixed_j2000 | 0.00943214 | 0.00940243 | 38014.4 | 3.011 | 3,592 |
| baseGR+SB16+NG:merged+J2iau | 0.00179122 | 0.000722285 | 38014.4 | 3.002 | 3,592 |

### Interpolation and pole shifts

| Comparison | Branch | Max position shift, km | Endpoint shift, km |
| --- | --- | ---: | ---: |
| daily → merged | baseGR+SB16 | 0.0455965 | 0.0455965 |
| daily → merged | baseGR+SB16+NG | 0.0455955 | 0.0455955 |
| fixed J2000 → IAU | baseGR+SB16 | 0.00927028 | 0.00927028 |
| fixed J2000 → IAU | baseGR+SB16+NG | 0.00927031 | 0.00927031 |

### IAU pole half-step sensitivity

| Branch variant | Max position difference, km | Endpoint difference, km | Coarse/fine RK4 steps |
| --- | ---: | ---: | ---: |
| baseGR+SB16:merged+J2iau | 0.00113614 | 0.00113614 | 3,592/7,088 |
| baseGR+SB16+NG:merged+J2iau | 0.00113709 | 0.00113709 | 3,592/7,088 |

## apophis_2029_long365d

Grid: 797 samples from 2029-01-01T00:00:00 to 2030-01-01T00:00:00 TDB; reference grid minimum is 38014.448 km at 2029-04-13T21:45:00.

| Variant | Max position error, km | Endpoint error, km | Grid min, km | Runtime, s | RK4 steps |
| --- | ---: | ---: | ---: | ---: | ---: |
| baseGR+SB16:daily | 377.359 | 377.359 | 38014.2 | 18.989 | 24,235 |
| baseGR+SB16:merged | 359.482 | 359.482 | 38014.2 | 18.963 | 24,235 |
| baseGR+SB16:merged+J2fixed_j2000 | 1698.67 | 1698.67 | 38014.1 | 20.042 | 24,235 |
| baseGR+SB16:merged+J2iau | 1701.89 | 1701.89 | 38014.1 | 20.096 | 24,235 |
| baseGR+SB16+NG:daily | 1870.17 | 1870.17 | 38014.6 | 19.492 | 24,235 |
| baseGR+SB16+NG:merged | 1814.69 | 1814.69 | 38014.5 | 19.956 | 24,235 |
| baseGR+SB16+NG:merged+J2fixed_j2000 | 9.96109 | 9.96109 | 38014.4 | 20.276 | 24,235 |
| baseGR+SB16+NG:merged+J2iau | 13.1481 | 13.1481 | 38014.4 | 20.359 | 24,235 |

### Interpolation and pole shifts

| Comparison | Branch | Max position shift, km | Endpoint shift, km |
| --- | --- | ---: | ---: |
| daily → merged | baseGR+SB16 | 56.7945 | 56.7945 |
| daily → merged | baseGR+SB16+NG | 56.7976 | 56.7976 |
| fixed J2000 → IAU | baseGR+SB16 | 3.33031 | 3.33031 |
| fixed J2000 → IAU | baseGR+SB16+NG | 3.33038 | 3.33038 |

### IAU pole half-step sensitivity

| Branch variant | Max position difference, km | Endpoint difference, km | Coarse/fine RK4 steps |
| --- | ---: | ---: | ---: |
| baseGR+SB16:merged+J2iau | 1.998 | 1.998 | 24,235/48,300 |
| baseGR+SB16+NG:merged+J2iau | 1.9978 | 1.9978 | 24,235/48,300 |

## Limitations

The pole convention is an approximate mean-pole text-PCK model and does not reproduce full Earth orientation, precession-nutation, EOP, or exact Horizons force/rotation conventions. Earth and Moon interpolation is cubic Hermite over the supplied rows. The high-cadence rows are used only in the existing 36-hour window; the long case retains its daily 2029 backbone elsewhere. Numerical half-step differences are empirical sensitivity checks rather than rigorous integration error bounds.

## Интерпретация результатов — 2026-09-07

В коротком 36-часовом окне J2 устраняет основную часть прежнего расхождения:
максимальная ошибка no-NG ветки падает с 3.65057 km до 0.00184481 km,
конечная — до 0.000768364 km. Но coarse/fine разница достигает 0.00113614 km.
Поэтому результат подтверждает существенность Earth J2, а не доказанную
субметровую точность модели или интегратора.

При старте 2029-01-01 тот же эффект взаимодействует с накопленной ошибкой до
сближения. В основной ветке без fitted NG J2 увеличивает конечную ошибку с
359.482 до 1701.887 km. В отдельной teacher-matching ветке с nominal NG J2
уменьшает её с 1814.69 до 13.1481 km. Частичная компенсация пропущенных сил
может делать менее полную модель ближе к конкретному эталону; такую модель
нельзя автоматически считать физически более верной.

Для годового J2 rollout уменьшение шага даёт около 1.998 km разницы.
Добавление локальных Earth/Moon узлов меняет годовой endpoint примерно на
56.8 km; изменение fixed J2000 pole на mean pole даты — на 3.33 km.
Это размеры сдвигов между расчётами, а не независимые оценки ошибки каждого
компонента. Причина оставшегося расхождения не установлена; full EIH,
более точная ориентация Земли и независимый solver ещё не проверены.

Apophis с длинным rollout сохраняется как stress case. Нельзя переносить
успех короткого окна на годовой прогноз или объявлять наиболее полную модель
гарантированным fallback для допусков 0.1–10 km.

## Воспроизведение

```bash
env UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never uv run --no-project --python-preference only-system python -B src/run_earth_j2_audit.py --root . --config configs/model_sufficiency_pilot6.json
```

Численные таблицы заново создаются в `outputs/earth_j2/pilot6_report.md`;
этот документ сохраняет их копию и отдельную интерпретацию. JSON содержит
config snapshot, source hashes и знаковые ошибки grid-min distance/time.
