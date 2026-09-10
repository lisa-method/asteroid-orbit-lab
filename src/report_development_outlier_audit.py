"""Render the verified post-hoc geography and force-ablation report."""
import hashlib
import json
from pathlib import Path


def main():
    root=Path(__file__).resolve().parents[1]
    out=root/"outputs/development30_outlier_audit"
    verification=json.loads((out/"verification.json").read_text())
    assert verification["passed"]
    for name,key in (("forces.json","forces_sha256"),("geography.json","geography_sha256")):
        assert hashlib.sha256((out/name).read_bytes()).hexdigest()==verification[key]
    forces=json.loads((out/"forces.json").read_text())
    geo=json.loads((out/"geography.json").read_text())
    for relative,expected in forces["provenance"]["source_hashes"].items():
        assert hashlib.sha256((root/relative).read_bytes()).hexdigest()==expected,relative
    geography_lines=[]
    for ident,name in (("153814","2001 WN5"),("613569","2006 TU7")):
        g=geo["objects"][ident]
        closest=g["planet_minima"][0]
        geography_lines.append(f"| {ident} ({name}) | {g['sampled_solar_min_au']:.3f}–{g['sampled_solar_max_au']:.3f} | {closest['body_name']}: {closest['distance_km']:,.0f} km | {g['pluto_minimum']['distance_au']:.2f} |")
    window_lines=[]
    for r in sorted(verification["failed_windows_rechecked"],key=lambda r:(r["object_id"],r["horizon_days"])):
        window_lines.append(f"| {r['object_id']} | {r['horizon_days']} | {r['variant']} | {r['old_error_km']:.6f} | {1000*r['new_error_km']:.3f} | {1000*r['step_difference_km']:.3f} |")
    force_lines=[]
    for ident,obj in forces["objects"].items():
        primary="EarthJ2" if ident=="153814" else "nominalNG"
        for name in ("baseline",primary,"Pluto",primary+"+Pluto"):
            variant=obj["variants"][name]
            annual=variant["horizons"]["365"]
            force_lines.append(f"| {ident} | {name} | {annual['max_position_error_km']:.9f} | {annual['production_fine_difference_km']*1000:.3f} |")
    pluto_lines=[]
    for r in verification["pluto_shifts"]:
        pluto_lines.append(f"| {r['object_id']} | {r['max_production_shift_m']:.3f} | {r['max_fine_shift_m']:.3f} | {r['max_paired_step_difference_m']:.3f} |")
    text=f"""# Два outlier-объекта: география и адресная проверка сил

2026-09-07. **Все пять ранее неразрешимых при 1 km окон проходят также
0.1 km после адресного добавления Earth J2 либо nominal NG.** Это post-hoc
физический аудит двух уже просмотренных объектов; исходная оценка селектора
55/60 и frozen development30 artifacts остаются неизменными.

## Где находятся объекты

Диапазон расстояний ниже измерен по reference в наших годовых окнах,
а не взят из старой эпохи osculating elements. 1 AU ≈149.6 млн km.

| Объект | Расстояние от Солнца, AU | Ближайшая планета в окне | Минимум до Pluto system, AU |
|---|---:|---|---:|
{chr(10).join(geography_lines)}

153814: окно 2028-05-27–2029-05-27; основное Earth encounter 2028-06-26.
Он проходит внутреннюю Солнечную систему и доходит до внутренней области
главного пояса. Header имеет a≈1.712 AU, q≈0.912 AU, Q≈2.511 AU,
но эти элементы относятся к 2017-11-26. Минимум до Луны в нашем окне
около 503 тыс. km; Луна уже присутствует в исходной модели.

613569: окно 2028-01-04–2029-01-03; Venus encounter 2028-02-03.
Его орбита охватывает область вокруг орбит Венеры и Земли, с приближением
к Солнцу примерно до 0.45 AU. Header a≈0.851 AU, q≈0.451 AU, Q≈1.252 AU
относится к 2020-09-13. Это не объект внешней Солнечной системы.

Минимум до Jupiter system — 5.13/4.97 AU, до Saturn system — 7.94/8.23 AU
для 153814/613569. Отдельных близких пролётов возле спутников гигантов здесь
нет. Массы систем гигантов уже включают их спутники в приближении общей
точечной массы в барицентре; детальная геометрия спутников отдельно не решается.

Из 16 включённых астероидных perturbers ближе всего Iris: 0.494/0.772 AU.
Ceres, Vesta и остальные выбранные массивные астероиды уже присутствуют в
baseline. Это не поиск ближайшего из всех известных малых астероидов.

## Какие изменения закрыли пять окон

Ошибка — максимум на исходной daily + five-minute reference сетке.
Разница шагов — максимум расстояния production/fine траекторий на той же сетке.
Сохраняются исходные gates: error ≤ tolerance и step difference ≤10% tolerance.

| Объект | Горизонт, суток | Добавленная сила | Было, km | Стало, m | Разница шагов, m |
|---|---:|---|---:|---:|---:|
{chr(10).join(window_lines)}

**153814: Earth J2.** Исходная модель представляла Землю точечной массой;
J2 учитывает ведущую поправку от её несферичного поля. Добавлены direct и
indirect члены в гелиоцентрической системе, прежние J2=0.00108262545,
радиус 6378.1366 km и приближённый IAU mean pole. Никакие параметры по
остаткам этого объекта не подгонялись. У исходного прогноза ошибка на 30-й
день около 0.88 m, на 31-й — 13.9 m; первое sampled превышение 1 km — день 89.
Большое уменьшение остатка после J2 согласуется с усилением небольшой ошибки
динамики пролёта в последующем движении. Остаточные метры этим не объяснены полностью.

**613569: nominal A2.** В immutable JPL#47 header обнаружены
`A2=1.18779071272e-13 AU/day²`, `A1=A3=0`, `ALN=1`, `NM=2`, `NK=0`,
`NN=5.093`, `R0=1`. Это поперечная негравитационная поправка с законом r⁻²,
которая была намеренно исключена из исходной четырёхмодельной карты.
Добавлено ровно сохранённое значение, без подгонки. Первое sampled превышение
1 km у baseline возникает на 142-й день. За год ошибка снизилась с 12.09 km
до 13.5 m. Такая A2-модель может описывать эффект Ярковского и другие
накопительные поперечные ускорения; этот опыт не устанавливает конкретный
физический механизм по одному fitted коэффициенту.
[Определение NG в Horizons](https://ssd.jpl.nasa.gov/horizons/manual.html#user-specified-small-bodies).

Orbit solution для 613569 датировано 2024-09-29, наблюдения 2002–2024,
то есть раньше нашего начала 2028-01-04. Однако его включение в набор моделей
выбрано после просмотра validation. Перенос в operational pipeline требует
явного контракта доступности A2 и его неопределённости; исходный selector
этот параметр не использовал и новым аудитом не переоценивался.

## Контроль гипотезы о Плутоне

Загружены 520 daily узлов Pluto system barycentre (body 9), Sun-centred
ICRF/FRAME, geometric, TDB, AU/day. GM=975.5 km³/s² из
[JPL astrodynamic parameters](https://ssd.jpl.nasa.gov/astro_par.html).
Гелиоцентрическая сила — разность воздействия на астероид и Солнце;
солнечный monopole второй раз не добавляется. Масса Харона отдельно не добавляется.

| Объект | Variant поверх baseline | Годовая max error, km | Production/fine, m |
|---|---|---:|---:|
{chr(10).join(force_lines)}

Для оценки именно вклада Плутона сравниваем векторы траекторий с ним и без него,
а не только разность скалярных max errors:

| Объект | Max shift от Pluto, production, m | На fine, m | Изменение парной поправки при уменьшении шага, m |
|---|---:|---:|---:|
{chr(10).join(pluto_lines)}

На этих окнах вклад Плутона меньше метра и не объясняет километровые отказы.
Его наличие в Солнечной системе не делает его первоочередной поправкой для
любого объекта. Для субметровой задачи эта оценка сама нуждается в более
строгой численной проверке и независимом solver.

## Проверки, воспроизведение и следующий шаг

Исходный B3+GR+SB16 повторён тем же новым propagation path до дополнительных
вариантов. Max errors и step differences на всех горизонтах прошли gate
1e-5 km против frozen results. Source/input hashes проверены.
Затем по accepted traces пересчитаны {verification['recomputed_position_and_velocity_samples']}
позиций/скоростей для сверки errors, shifts и eligibility. Все 104 raw-файла
прошли SHA-256/size и Git exclusions. 82 unit tests прошли, включая проверки
гелиоцентрической компенсации, независимости epoch closures и NG headers.
Read-only review force composition проведён; независимого динамического
solver по-прежнему нет. Строгий межузловой bound не заявляется.

Команды из корня; `dev_python` определён в
[development30 reproduction](DEVELOPMENT30_REPRODUCIBILITY.md):

```bash
dev_python src/prepare_outlier_pluto.py
dev_python src/diagnose_development_outlier_geometry.py
dev_python src/audit_development_outlier_forces.py --root . --pluto-path data/raw/development30_outlier_audit/pluto_system_daily.json --pluto-gm-km3-s2 975.5
dev_python src/verify_development_outlier_audit.py
dev_python src/report_development_outlier_audit.py
```

Первый вызов downloader при отсутствии cache обращается к публичному JPL;
остальные работают локально. Force audit сохраняет checkpoints, но автоматического
resume нет; повтор означает повтор полного короткого аудита. Не редактировать
numerical sources между расчётом и проверкой. Результаты исходного development30
не перезаписываются. Forces SHA-256: `{verification['forces_sha256']}`.
Контракт: [DEVELOPMENT30_OUTLIER_AUDIT_CONTRACT.md](DEVELOPMENT30_OUTLIER_AUDIT_CONTRACT.md).

Дальше стоит заморозить новую версию кандидатов с Earth J2 и известными NG
параметрами, проверить их на остальном development set и измерить стоимость.
Затем проверить обновлённый выбор на свежих объектах: пять нынешних окон уже
использованы для диагностики. Точность ниже 10–30 m требует отдельного аудита
остатка, внешних эфемерид, полного релятивистского описания и независимого solver;
это следующий уровень требований, а не причина задерживать вывод о 1 km.
"""
    for path in (out/"report.md",root/"docs/DEVELOPMENT30_OUTLIER_AUDIT_REPORT.md"):
        path.write_text(text)
    print(root/"docs/DEVELOPMENT30_OUTLIER_AUDIT_REPORT.md")


if __name__=="__main__":
    main()
