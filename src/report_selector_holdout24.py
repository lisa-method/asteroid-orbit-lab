"""Build a factual Markdown report from the immutable holdout artifacts."""
from pathlib import Path
from collections import Counter
import hashlib
import json

OUT='outputs/selector_holdout24'
LABELS={'tree_guard':'CART + предупреждение','horizon_v2':'Правило по горизонту v2',
        'horizon_guard':'Правило + предупреждение','fixed_full':'Постоянная полная модель'}

def report(root):
    read=lambda p:json.loads((root/p).read_text())
    matrix=read(OUT+'/matrix.json');cost=read(OUT+'/direct_cost.json');verification=read(OUT+'/verification.json')
    sample=read('data/processed/selector_holdout24/sample.json');raw=read(OUT+'/raw_verification_python314.json')
    failure=read(OUT+'/failure_diagnostic.json')
    model=read('outputs/selector_v3/model.json')
    sha=lambda p:hashlib.sha256((root/p).read_bytes()).hexdigest()
    if not raw['passed'] or not verification['passed'] or verification['matrix_sha256']!=sha(OUT+'/matrix.json') or verification['timing_sha256']!=sha(OUT+'/direct_cost.json'):
        raise ValueError('Finish verification before reporting')
    if failure['verification']!='passed' or failure['source_sha256'][OUT+'/matrix.json']!=sha(OUT+'/matrix.json'):
        raise ValueError('Failure diagnosis must match the frozen matrix')
    train_features=[]
    for oid in model['training_object_ids']:
        path=f'outputs/selector_v3/development_features/object_{oid}.json'
        if sha(path)!=model['feature_hashes'][path]:raise ValueError('Changed training feature file')
        train_features.extend(read(path)['rows'])
    train_small_max=10**max(r['vector'][5] for r in train_features)
    cases=matrix['records'];choices=matrix['choices'];primary=[c for c in choices if c['tolerance_km']==1.0]
    summaries={(r['method'],r['tolerance_km']):r for r in matrix['summaries']}
    full=cost['full_cost']['fixed_full']['total_seconds'];tree=cost['full_cost']['tree_guard']['total_seconds']
    lines=['# Обучаемый выбор физики: проверка на 24 новых телах — 2026-09-09','',
      'Метод и параметры были зафиксированы до выбора новых объектов и загрузки их траекторий. '
      'Проверены 24 уникальных тела, 5 зависимых горизонтов на тело и 4 физических кандидата: '
      '480 model/case records, 192 годовых production/fine traces. Сравниваются ошибки '
      'относительно model-derived Horizons при заданном initial state, а не сырые наблюдения.','',
      '## Главный допуск: 1 km','',
      '| Метод | Успешных окон / 120 | Тел со всеми 5 успешными горизонтами / 24 | Максимальная ошибка | Полное время | Изменение времени к full |',
      '| --- | ---: | ---: | ---: | ---: | ---: |']
    for method,label in LABELS.items():
        sm=summaries[(method,1.0)];seconds=cost['full_cost'][method]['total_seconds']
        body_pass=sum(all(c['actual_eligible'] for c in primary if c['method']==method and c['object_id']==obj['id']) for obj in sample['objects'])
        lines.append(f"| {label} | {sm['eligible_cases']} / 120 | {body_pass} / 24 | {sm['worst_position_error_km']:.6g} km | {seconds:.3f} s | {(seconds/full-1)*100:+.2f}% |")
    best_rule=min(('horizon_v2','horizon_guard'),key=lambda m:cost['full_cost'][m]['total_seconds'])
    rule_seconds=cost['full_cost'][best_rule]['total_seconds']
    tree_summary=summaries[('tree_guard',1.0)];rule_summary=summaries[(best_rule,1.0)]
    lines+=['',f"CART проходит {tree_summary['eligible_cases']} окон; самое быстрое простое правило "
      f"({LABELS[best_rule]}) — {rule_summary['eligible_cases']}. "
      f"Отношение времени CART к этому правилу: {tree/rule_seconds:.4f}; "
      f"изменение {(tree/rule_seconds-1)*100:+.2f}%. "
      'Это прямое сравнение добавочной пользы обучения; экономия относительно full сама по себе '
      'ещё не доказывает превосходство ML над простым правилом. '
      'При разном числе успешных окон это компромисс стоимости и точности, '
      'а не ускорение при неизменном выполнении всех требований.','',
      'Время — сумма медиан трёх прямых повторов каждого из 120 окон. '
      'Всего выполнено 1440 вызовов; порядок методов чередовался. Внутри учитываются '
      'построение признаков там, где оно нужно, inference, сборка сил и rollout. '
      'Общее чтение эфемерид, загрузка по сети, offline fine audit и evaluator исключены. '
      'Результаты прямых прогнозов сверены с годовой матрицей; это не сумма условных цен lookup. '
      'Три повтора на одной машине не задают статистическую гарантию ускорения на другом оборудовании.','',
      '## Выбранные кандидаты при 1 km','',
      '| Метод | B2 | P | P + GR | P + GR + SB16 |',
      '| --- | ---: | ---: | ---: | ---: |']
    for method,label in LABELS.items():
        counts=Counter(c['model_id'] for c in primary if c['method']==method)
        lines.append('| '+label+' | '+' | '.join(str(counts[m]) for m in ('V2-B2','V2-P','V2-P-GR','V2-P-GR-SB16'))+' |')
    lines+=['','B2 — Солнце и тестовое тело. P добавляет девять главных возмущающих тел, '
      'Earth J2 и доступные NG; GR — солнечную релятивистскую поправку; SB16 — '
      '16 массивных малых тел. Точный состав и соглашения заданы frozen v2.','',
      '## Время по горизонту при 1 km','',
      '| Горизонт | CART + предупреждение | Правило v2 | Правило + предупреждение | Full |',
      '| --- | ---: | ---: | ---: | ---: |']
    for h in (7.,30.,90.,180.,365.):
        seconds=[sum(r['runtime_median_seconds'] for r in cost['timings'] if r['method']==m and r['horizon_days']==h) for m in LABELS]
        lines.append(f'| {h:g} d | '+' | '.join(f'{s:.3f} s' for s in seconds)+' |')
    annual_full=sum(c['model_id']=='V2-P-GR-SB16' for c in primary if c['method']=='tree_guard' and c['horizon_days']==365.0)
    lines+=['','Каждая ячейка суммирует 24 прогноза данного горизонта. '
      f'На 365 сутках CART выбирает full для {annual_full} из 24 тел; '
      'надёжного выигрыша по времени на этом горизонте не видно. Основная '
      'экономия возникает на коротких и средних горизонтах.','',
      '## Зависимость от допуска','',
      '| Метод | 0.1 km | 1 km | 10 km |', '| --- | ---: | ---: | ---: |']
    for method,label in LABELS.items():
        values=[summaries[(method,t)]['eligible_cases'] for t in (.1,1.,10.)]
        lines.append(f'| {label} | {values[0]} / 120 | {values[1]} / 120 | {values[2]} / 120 |')
    lines+=['','Успех требует одновременно max sampled position error ≤ допуск и '
      'production/fine position difference ≤ 10% допуска. Максимум на сетке не является '
      'continuous-time гарантией. Прямые замеры времени сделаны только при 1 km.','',
      '## Отказы и предупреждения','',
      '| Допуск | Нет достаточного кандидата | Ошибки выбора CART при наличии кандидата | Предупреждений CART | Пропусков no-candidate |',
      '| --- | ---: | ---: | ---: | ---: |']
    for t in (.1,1.,10.):
        sm=summaries[('tree_guard',t)]
        lines.append(f"| {t:g} km | {sm['no_candidate_cases']} | {sm['avoidable_selection_misses']} | {sm['warning_cases']} | {sm['warning_vs_no_candidate']['fn']} |")
    lines+=['','Срабатывание strong-encounter guard означает неподтверждённую надёжность режима, '
      'а не доказанную ошибку прогноза. В machine-readable summaries отдельно сохранены TP/FN/FP/TN '
      'относительно фактической недостаточности всего набора, ошибки среди unflagged '
      'и предупреждения при фактически точном результате. При отсутствии no-candidate '
      'случаев нельзя считать надёжность их распознавания проверенной.','',
      '## Контрпример: поясное тело 3418','',
      'Все три ошибки CART относятся к разным горизонтам одного тела. '
      'Это post-hoc разбор замороженной проверки; правило после него не меняется.','',
      '| Допуск | Горизонт | Выбор CART | Предсказанная оценка ошибки | Фактическая ошибка | Ошибка full |',
      '| --- | ---: | --- | ---: | ---: | ---: |']
    for row in failure['failures']:
        if row['method']!='tree_guard':continue
        cap=row['reevaluated_tree_decision']['predicted_error_caps_km'][row['selected_model_id']]
        full_error=row['all_candidate_errors']['V2-P-GR-SB16']['max_position_error_km']
        lines.append(f"| {row['tolerance_km']:g} km | {row['horizon_days']:g} d | {row['selected_model_id']} | {cap:.6g} km | {row['actual_selected_max_position_error_km']:.6g} km | {full_error*1000:.6g} m |")
    lines+=['','3418 начинает прогноз на гелиоцентрическом расстоянии 3.059 AU; '
      'это внешняя поясная control stratum. При 90 d small-body proxy составляет '
      '1.871 km, но дерево P использует только GR proxy и оценивает ошибку в '
      '0.315 km вместо фактических 1.695 km. Полная модель при том же initial '
      'даёт 0.351 m: доступного набора сил достаточно. При 365 d дерево P+GR '
      'также недооценивает остаток (7.122 против 13.102 km). Ни один из этих '
      'промахов не отмечен warning. Изменение шага даёт миллиметровые различия '
      'траекторий, намного меньшие наблюдаемых ошибок выбора.','',
      f'Максимальный small-body proxy во всех 90 train cases — {train_small_max:.6g} km. '
      'У 3418 он равен 0.202/1.871/18.143 km на 30/90/365 сутках. '
      'На 30 сутках значение внутри общего train range, но выше диапазона '
      'обучающих 30-day cases; на 90 и 365 сутках — уже выше общего train maximum. '
      'Это диагностирует ограниченную поддержку в обучении. Сам по себе такой '
      'признак не является доказанной ошибкой или валидированным правилом отказа.','',
      'Разность между P+GR и full изолирует добавление группы SB16, но не '
      'указывает отдельное возмущающее тело. Для следующей версии нужен общий '
      'контроль суммарно пропущенных сил и выхода за обученный диапазон; '
      'исключение по номеру 3418 не является проверенным решением.','',
      '## Состав выборки и годовые прогнозы CART при 1 km','',
      '| Тело | Группа | Начало | Выбранная физика | Ошибка max | Numerical difference | Статус |',
      '| --- | --- | --- | --- | ---: | ---: | --- |']
    look={(r['object_id'],r['horizon_days'],r['model_id']):r for r in cases}
    for obj in sample['objects']:
        ch=next(c for c in primary if c['object_id']==obj['id'] and c['horizon_days']==365.0 and c['method']=='tree_guard')
        lines.append(f"| {obj['name']} | {obj['stratum']} | {obj['start_date']} | {ch['model_id']} | {ch['max_position_error_km']*1000:.3f} m | {ch['numerical_difference_km']*1000:.3f} m | {ch['status']} |")
    lines+=['','По четыре тела в Earth/Venus/Mars/Jupiter encounters и двух поясных control strata. '
      'Исключены прежние pilot, development30, fresh12, Geographos и 16 massive perturbers. '
      'Выбор metadata-only: зафиксированный порядок страт и сортировки, без forecast errors. '
      'Каталожные расстояния сближений этой выборки значительно больше, чем для пролёта Апофиса; '
      'она не валидирует экстремальное рассеяние. '
      '120 окон — это 24 тела с зависимыми горизонтами, не 120 независимых объектов.','',
      '## Близкие сближения','',
      '| Тело | Планета | Ошибка расстояния CART | Ошибка времени CART | Production/fine по расстоянию |',
      '| --- | --- | ---: | ---: | ---: |']
    for obj in sample['objects']:
        if obj['event'] is None:continue
        ch=next(c for c in primary if c['object_id']==obj['id'] and c['horizon_days']==365.0 and c['method']=='tree_guard')
        geom=look[(obj['id'],365.0,ch['model_id'])]['record']['closest_geometry']
        lines.append(f"| {obj['id']} | {obj['stratum']} | {geom['errors']['distance_km']*1000:.3f} m | {geom['errors']['time_days']*86400:.6f} s | {geom['production_fine_shift']['distance_km']*1000:.3f} m |")
    lines+=['','Каждое событие оценивается в своём фиксированном четырёхсуточном окне. '
      'Прогнозный минимум ищется между accepted endpoints; reference имеет шаг 5 min. '
      'Малые различия времени между интерполированными минимумами не доказывают такую же '
      'абсолютную точность относительно истинного физического события. Даты каталожных '
      'событий используются только sampler/evaluator, не feature builder или force rollout.','',
      '## Что обучено и чего это не доказывает','',
      'Четыре CART regressors глубины 3 оценивают log effective error физических '
      'кандидатов. Train — прежние 18 тел; отдельный максимальный положительный log residual '
      'на прежних 12 телах задаёт эмпирический запас. Эти 30 тел уже изучены и не '
      'выдаются за новый test. Стоимость кандидатов для выбора — train median. '
      'Входы: initial state, exogenous ephemerides, доступные NG, horizon и tolerance. '
      'Номер астероида не является признаком. Признаки будущей истинной траектории не используются.','',
      'В обученных деревьях B2 все разбиения используют planet proxy, а у P — GR proxy. '
      'Для P+GR преобладает small-body proxy; в полном кандидате используются три force proxies. '
      'Это согласуется с задачей оценки пропущенных сил, но не устанавливает причинность. '
      'Не все девять входных признаков используются деревьями: геометрический guard работает отдельно.','',
      'Эмпирический запас не является доверительным интервалом 95%. '
      '`accuracy_guaranteed=False`, `conditional_on_initial_state=True`, '
      '`covariance_calibrated=False`. Предсказание координат выполняет физический '
      'интегратор; ML выбирает физическую модель на весь rollout. Наблюдательная '
      'калибровка uncertainty и универсальная гарантия для всей популяции не получены.','',
      'Апофис остаётся отдельным изученным диагностическим случаем: годовой остаток '
      'около 2.552 km, несмотря на новый независимый численный контроль. Local restart '
      'снова даёт остаток через пролёт; строгая метровая сходимость длинного covariance '
      'переноса не достигнута. Guard срабатывает на его 180/365-day forecasts, но '
      'это regression, не новая оценка. См. [отчёт Апофиса](APOPHIS_ENCOUNTER_CLOSURE_REPORT.md).','',
      '## Артефакты и воспроизведение','',
      f"Method freeze SHA256: `{sha('outputs/selector_v3/method_freeze.json')}`.",
      f"Проверены {verification['error_rows']:,} error samples, 64 event-geometry records, 192 traces и 480 timing medians.",
      f"Raw audit: {raw['inventory']['unique_raw_paths']} уникальный путь в {raw['inventory']['manifest_count']} manifests; 40 новых target tables и 9 byte-identical reused planet files.",
      '',
      '- `outputs/selector_v3/model.json` — обученные деревья и margins.',
      '- `outputs/selector_v3/training_verification.json` — exact deterministic refit и hashes.',
      '- `outputs/selector_holdout24/matrix.json` — полная физическая матрица и 1440 решений.',
      '- `outputs/selector_holdout24/direct_cost.json` — прямые замеры и сверка actual rollouts.',
      '- `outputs/selector_holdout24/verification.json` — пересчёт метрик из traces.',
      '- `outputs/selector_holdout24/failure_diagnostic.json` — post-hoc ошибки выбора и replay caps.',
      '- `outputs/selector_holdout24/raw_verification_python314.json` — provenance и Git exclusions всех raw.',
      '',
      '[Контракт](SELECTOR_V3_CONTRACT.md), [программный API](SELECTOR_V3_USAGE.md). '
      'Python 3.14.7 через существующий uv, стандартная библиотека. Новые зависимости, '
      'окружение, commit или публикация не создавались. Визуальное оформление оставлено '
      'на отдельное обсуждение с пользователем.','',
      '```sh',
      'env PYTHONPATH=src UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never uv run --no-project --python-preference only-system --python 3.14.7 python -B src/run_selector_holdout24_compact.py verify',
      '```','',
      'Для незавершённых этапов используются `matrix --resume` и `cost --resume`. '
      'Завершённые artifacts не перезаписываются; после открытия holdout метод не менялся. '
      'Compact wrapper меняет только консольный вывод и вызывает исходные frozen функции.']
    text='\n'.join(lines)+'\n'
    target=root/'docs/SELECTOR_HOLDOUT24_REPORT.md';target.write_text(text)
    return {'report':str(target),'tree_seconds':tree,'fixed_seconds':full,'tree_pass1km':summaries[('tree_guard',1.0)]['eligible_cases']}

if __name__=='__main__':print(json.dumps(report(Path('.').resolve()),ensure_ascii=False))
