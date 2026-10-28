## fdb-delegate-strict (exact match)

| metric | value |
| --- | --- |
| pass rate (FDB-v3, strict) | 28/100 (28%) |
| tool selection F1 (mean) | 0.567 |
| argument accuracy (mean) | 0.323 |
| items with a handoff | 58/100 (58%) |
| FDB calls made by frontend / backend | 0 / 92 |
| unrelated harness tool calls | 0 |
| errors | 0 |
| frontend first token, median (s) | 0.44 |
| end to end, median (s) | 5.92 |

| difficulty | pass rate |
| --- | --- |
| easy | 9/36 (25%) |
| medium | 11/34 (32%) |
| hard | 8/30 (27%) |
| self-correction items | 6/17 (35%) |

Failures:

- ecommerce_01_65e8cf8f4c7424fa062e54a3: Missing tools: ['track_order']
- ecommerce_02_5f4a4da1575d605c43bef871: Missing tools: ['search_products']
- ecommerce_04_6998abd731d2ec50d067d5bd: Wrong arguments for: ['track_order']
- ecommerce_06_66c4f3cb14cbfc4db836bd4e: Missing tools: ['add_to_cart']
- ecommerce_08_5ff07b5ee7a1d23e719e421e: Missing tools: ['search_products']
- ecommerce_08_61517db6a7589569521b2356: Missing tools: ['search_products']
- ecommerce_11_62a885d5b6af18b3d4579e1b: Missing tools: ['add_to_cart']
- ecommerce_12_6998abd731d2ec50d067d5bd: Missing tools: ['search_products']
- ecommerce_13_5ff07b5ee7a1d23e719e421e: Wrong arguments for: ['add_to_cart', 'track_order']
- ecommerce_14_5ff07b5ee7a1d23e719e421e: Wrong arguments for: ['add_to_cart']
- ecommerce_15_56780a5a89319e0011644cb4: Missing tools: ['track_order']
- ecommerce_15_66c4f3cb14cbfc4db836bd4e: Missing tools: ['track_order']
- ecommerce_16_695bd157114f0d2317f88617: Missing tools: ['search_products']
- ecommerce_18_62a885d5b6af18b3d4579e1b: Wrong arguments for: ['search_products', 'track_order']
- ecommerce_19_66f59c766e7e22e1f90d08f6: Missing tools: ['search_products', 'add_to_cart']
- ecommerce_20_66c4f3cb14cbfc4db836bd4e: Wrong arguments for: ['search_products', 'add_to_cart']
- ecommerce_21_65e8cf8f4c7424fa062e54a3: Missing tools: ['add_to_cart']
- ecommerce_21_69a9cf80f4d7668d5c815038: Missing tools: ['add_to_cart']
- finance_03_5ff07b5ee7a1d23e719e421e: Missing tools: ['modify_autopay']
- finance_04_62a885d5b6af18b3d4579e1b: Wrong arguments for: ['get_exchange_rate']
- finance_06_66f59c766e7e22e1f90d08f6: Missing tools: ['get_card_benefits']
- finance_07_5ff07b5ee7a1d23e719e421e: Missing tools: ['modify_autopay']
- finance_07_61517db6a7589569521b2356: Missing tools: ['modify_autopay']
- finance_08_65e8cf8f4c7424fa062e54a3: Missing tools: ['get_exchange_rate']
- finance_08_69a9cf80f4d7668d5c815038: Missing tools: ['get_exchange_rate']
- finance_11_695bd157114f0d2317f88617: Missing tools: ['modify_autopay']
- finance_14_5f4a4da1575d605c43bef871: Missing tools: ['get_exchange_rate', 'get_exchange_rate']
- finance_15_66c4f3cb14cbfc4db836bd4e: Missing tools: ['modify_autopay']
- finance_22_5f4a4da1575d605c43bef871: Missing tools: ['modify_autopay', 'modify_autopay']
- housing_03_5f4a4da1575d605c43bef871: Wrong arguments for: ['update_search_filter']
- housing_04_695bd157114f0d2317f88617: Missing tools: ['search_apartments']
- housing_05_5ff07b5ee7a1d23e719e421e: Wrong arguments for: ['search_apartments']
- housing_05_61517db6a7589569521b2356: Wrong arguments for: ['search_apartments']
- housing_06_5f4a4da1575d605c43bef871: Missing tools: ['calculate_commute']
- housing_08_66f59c766e7e22e1f90d08f6: Wrong arguments for: ['update_search_filter']
- housing_10_66f59c766e7e22e1f90d08f6: Wrong arguments for: ['calculate_commute', 'update_search_filter']
- housing_11_62a885d5b6af18b3d4579e1b: Missing tools: ['search_apartments']
- housing_13_5f4a4da1575d605c43bef871: Wrong arguments for: ['update_search_filter', 'update_search_filter']
- housing_14_5ff07b5ee7a1d23e719e421e: Missing tools: ['search_apartments']
- housing_14_61517db6a7589569521b2356: Missing tools: ['search_apartments']
