"""Describe the frozen 100-object metadata cohort without reading forecasts."""
from __future__ import annotations
from collections import Counter
import json
from pathlib import Path
from prepare_selector_confirmation import check_hashes


def report(root):
    freeze=json.loads((root/'outputs/selector_confirmation100/experiment_freeze.json').read_text())
    check_hashes(root,freeze['hashes'])
    sample=json.loads((root/'data/processed/selector_confirmation100/sample.json').read_text())
    raw=json.loads((root/'outputs/selector_confirmation100/raw_verification.json').read_text())
    timing=set(sample['timing_object_ids']); values=[]
    for obj in sample['objects']:
        meta=json.loads((root/obj['metadata_path']).read_text())
        els={e['name']:float(e['value']) for e in meta['orbit']['elements']}
        values.append(dict(obj,**{k:els[k] for k in ('a','e','q','i')},orbit_class=meta['object']['orbit_class']['code'],
                           ng=raw['ng'][obj['id']]['status']))
    lines=['# Confirmation100: состав независимой выборки','',
           'Этот отчёт читает только зафиксированную выборку, каталожные метаданные и проверку входов. Ошибки прогноза не использованы для подбора тел или изменения квот. [Протокол](SELECTOR_CONFIRMATION100_CONTRACT.md).','',
           '## Покрытие режимов','',
           '| Страта | Тел | a, AU | q, AU | e | i, deg | Минимальная дуга наблюдений, d | NG available | Timing |',
           '| --- | ---: | --- | --- | --- | --- | ---: | ---: | ---: |']
    strata=list(dict.fromkeys(o['stratum'] for o in values))
    for s in strata:
        v=[o for o in values if o['stratum']==s]
        ranges=['–'.join(f'{f(vv[k] for vv in v):.4g}' for f in (min,max)) for k in ('a','q','e','i')]
        lines.append(f'| {s} | {len(v)} | '+ ' | '.join(ranges)+f" | {min(int(o['data_arc_days']) for o in v)} | {sum(o['ng']=='available' for o in v)} | {sum(o['id'] in timing for o in v)} |")
    classes=Counter(o['orbit_class'] for o in values);quality=Counter(o['condition_code'] for o in values)
    lines += ['',f'Каталожные orbit classes: {dict(sorted(classes.items()))}.',f'Condition codes: {dict(sorted(quality.items()))}.','',
              'Орбитальные элементы — osculating metadata на сохранённой эпохе SBDB каждого тела, не измеренные экстремумы будущей траектории. Они не заменяют одновременное расстояние до планеты. Condition code и длина дуги — критерии отбора, а не доверительные интервалы прогноза.','',
              'Выборка не покрывает кометы, физические спутники, все возможные двойные системы и транснептуновую популяцию. Квоты намеренно обогащают сложные режимы; общий процент успеха относится к этому benchmark. Пять вложенных горизонтов одного тела зависимы.','',
              '## Полный список','',
              '| ID | Страта | Класс | a, AU | q, AU | e | i, deg | Code | Arc, d | NG | Timing |',
              '| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |']
    for o in values:
        lines.append(f"| {o['designation']} | {o['stratum']} | {o['orbit_class']} | {o['a']:.5g} | {o['q']:.5g} | {o['e']:.5g} | {o['i']:.5g} | {o['condition_code']} | {o['data_arc_days']} | {o['ng']} | {'yes' if o['id'] in timing else '—'} |")
    lines += ['', '## Сближения, зафиксированные каталогом до прогнозов','',
              '| ID | Планета | TDB event | Номинальное расстояние, km | Скорость, km/s |','| --- | --- | --- | ---: | ---: |']
    for o in values:
        if o.get('event'):
            e=o['event'];speed=e['catalogue_row']['v_rel']
            lines.append(f"| {o['designation']} | {e['body_name']} | {e['cd']} | {float(e['dist_au'])*149597870.7:.3f} | {float(speed):.6f} |")
    lines += ['', 'Это выбранные catalog events, а не исчерпывающий перечень локальных минимумов. Evaluator уточняет их на отдельной сетке; селектор получает только initial state и exogenous inputs.','',
              'Воспроизведение: `PYTHONPATH=src uv run --no-project --python-preference only-system --python 3.14.7 python -B src/report_confirmation_sample.py`.']
    (root/'docs/SELECTOR_CONFIRMATION100_SAMPLE_AUDIT.md').write_text('\n'.join(lines)+'\n')
    return dict(objects=len(values),classes=dict(classes),condition_codes=dict(quality),report='docs/SELECTOR_CONFIRMATION100_SAMPLE_AUDIT.md')


if __name__=='__main__': print(json.dumps(report(Path.cwd()),ensure_ascii=False))
