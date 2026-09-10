# Проверка смены Python при возобновлении эксперимента

2026-09-07: сохранённый screening snapshot и первый прерванный planetary run
использовали Python 3.9.6. При продолжении тот же uv command выбрал установленный
Python 3.14.7. Полный повтор выполняется целиком на 3.14.7; старые timing rows
в итог не смешиваются. Внешние зависимости для этого не устанавливались.

На всех 648 evaluation rows из старого checkpoint и их повторе максимальная
разница forecast minimum distance составила 4.7684e-7 km, времени —
9.7734e-7 s. Outcomes совпали. Это наблюдаемое расхождение между запусками;
механизм изменения округления отдельно не исследовался.

До завершения оставшихся Apophis окон фиксируется проверка совместимости
геометрии B2 и reference с предыдущим snapshot:

- distance: absolute difference ≤ 1e-5 km (1 cm), эквивалентный порог в AU;
- time of minimum: absolute difference ≤ 0.01 s;
- relative speed: absolute difference ≤ 1e-8 km/s;
- eta и Hill proxy: relative tolerance 1e-10, absolute tolerance 1e-14;
- counts, IDs, flags и остальные metadata совпадают точно;
- alert outcomes пересчитываются независимо; научные пороги не меняются.

При одной версии Python ожидается прежняя exact regression. При разных
версиях exact equality показывается отдельно от bounded compatibility,
а максимальные фактические различия сохраняются. Несовпадение строгой
побитовой проверки нельзя называть exact PASS.

Это техническая проверка воспроизводимости при смене runtime, не новая
граница ошибки физической модели. Отдельные production/fine-step differences
и reference discrepancies продолжают оцениваться своим протоколом.

Source hashes основного запуска остаются неизменными. Если после завершения
запуска обновляется только verifier для поддержки cross-runtime comparison,
его прежний исходник сохраняется с исходным hash, а новый hash записывается
в verification metadata. Для численного кода и конфигураций такого исключения
нет. Следующие воспроизведения следует выполнять с явной версией Python,
записанной в results.json.

## Итог проверки

Полный 34-case запуск завершён на Python 3.14.7. Cross-runtime compatibility
прошла для всех 306 B2 minima и 306 reference minima. Максимальное отличие
B2 distance — 1.430511e-6 km (1.431 mm), времени — 1.682110e-6 s.
Reference distance отличается максимум на 9.536743e-7 km (0.954 mm), время
совпадает точно. Указанные выше бюджеты не расширялись.

Изначальный strict verifier действительно завершился с assertion на exact
B2 comparison. Он сохранён в ignored provenance. Обновлённый verifier явно
возвращает `exact: false`, фактические различия, исходную/текущую версии
Python и hashes обеих версий verifier. Все численные исходники и config
совпали с hashes начала/конца запуска; разрешённое исключение касается
только самого проверяющего скрипта. Все 62 unit tests прошли.
