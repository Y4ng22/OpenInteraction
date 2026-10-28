## funaudio-fdb-delegate (LLM judge)

| metric | value |
| --- | --- |
| pass rate (FDB-v3, strict) | 35/100 (35%) |
| tool selection F1 (mean) | 0.706 |
| argument accuracy (mean) | 0.753 |
| items with a handoff | 100/100 (100%) |
| FDB calls made by frontend / backend | 0 / 367 |
| unrelated harness tool calls | 0 |
| errors | 0 |
| end to end, median (s) | 11.43 |

| difficulty | pass rate |
| --- | --- |
| easy | 15/36 (42%) |
| medium | 16/34 (47%) |
| hard | 4/30 (13%) |
| self-correction items | 9/17 (53%) |

Failures:

- ecommerce_02_5f4a4da1575d605c43bef871: Unexpected tools: ['search_products', 'search_products', 'search_products']
- ecommerce_05_695bd157114f0d2317f88617: Unexpected tools: ['search_products', 'search_products', 'search_products']
- ecommerce_08_5ff07b5ee7a1d23e719e421e: Unexpected tools: ['search_products', 'search_products', 'search_products']
- ecommerce_08_61517db6a7589569521b2356: Unexpected tools: ['search_products', 'search_products', 'search_products']
- ecommerce_10_6998abd731d2ec50d067d5bd: Unexpected tools: ['search_products']
- ecommerce_12_6998abd731d2ec50d067d5bd: Unexpected tools: ['search_products', 'search_products', 'search_products']
- ecommerce_13_5ff07b5ee7a1d23e719e421e: Wrong arguments for: ['add_to_cart']
- ecommerce_13_61517db6a7589569521b2356: Unexpected tools: ['add_to_cart', 'track_order', 'add_to_cart', 'track_order', 'add_to_cart', 'track_order']
- ecommerce_14_5ff07b5ee7a1d23e719e421e: Wrong arguments for: ['add_to_cart']
- ecommerce_18_62a885d5b6af18b3d4579e1b: Unexpected tools: ['search_products', 'add_to_cart', 'track_order', 'search_products', 'add_to_cart', 'track_order', 'search_products', 'add_to_cart', 'track_order']
- ecommerce_19_66f59c766e7e22e1f90d08f6: Wrong arguments for: ['add_to_cart']
- ecommerce_20_66c4f3cb14cbfc4db836bd4e: Unexpected tools: ['search_products', 'add_to_cart', 'search_products', 'add_to_cart', 'search_products', 'add_to_cart']
- ecommerce_21_65e8cf8f4c7424fa062e54a3: Unexpected tools: ['track_order', 'search_products', 'add_to_cart', 'track_order', 'search_products', 'add_to_cart', 'track_order', 'search_products', 'add_to_cart']
- ecommerce_21_69a9cf80f4d7668d5c815038: Unexpected tools: ['track_order', 'search_products', 'add_to_cart', 'track_order', 'search_products', 'add_to_cart', 'track_order', 'search_products', 'add_to_cart']
- ecommerce_23_5f4a4da1575d605c43bef871: Unexpected tools: ['add_to_cart']
- ecommerce_25_65e8cf8f4c7424fa062e54a3: Unexpected tools: ['search_products', 'add_to_cart']
- ecommerce_25_69a9cf80f4d7668d5c815038: Unexpected tools: ['search_products', 'add_to_cart']
- finance_01_65e8cf8f4c7424fa062e54a3: Unexpected tools: ['get_exchange_rate', 'get_exchange_rate', 'get_exchange_rate']
- finance_01_69a9cf80f4d7668d5c815038: Unexpected tools: ['get_exchange_rate', 'get_exchange_rate', 'get_exchange_rate']
- finance_02_6998abd731d2ec50d067d5bd: Unexpected tools: ['get_card_benefits']
- finance_04_62a885d5b6af18b3d4579e1b: Unexpected tools: ['get_exchange_rate', 'get_exchange_rate', 'get_exchange_rate']
- finance_08_65e8cf8f4c7424fa062e54a3: Unexpected tools: ['get_exchange_rate', 'get_exchange_rate', 'get_exchange_rate']
- finance_08_69a9cf80f4d7668d5c815038: Unexpected tools: ['get_exchange_rate', 'get_exchange_rate', 'get_exchange_rate']
- finance_10_66c4f3cb14cbfc4db836bd4e: Unexpected tools: ['get_card_benefits', 'get_exchange_rate', 'get_card_benefits', 'get_exchange_rate', 'get_card_benefits', 'get_exchange_rate']
- finance_12_65e8cf8f4c7424fa062e54a3: Unexpected tools: ['get_exchange_rate', 'get_exchange_rate', 'get_exchange_rate']
- finance_12_69a9cf80f4d7668d5c815038: Unexpected tools: ['get_exchange_rate', 'get_exchange_rate', 'get_exchange_rate']
- finance_14_5f4a4da1575d605c43bef871: Unexpected tools: ['get_exchange_rate', 'get_exchange_rate', 'get_exchange_rate', 'get_exchange_rate', 'get_exchange_rate', 'get_exchange_rate']
- finance_18_6998abd731d2ec50d067d5bd: Unexpected tools: ['get_exchange_rate', 'modify_autopay', 'get_card_benefits', 'get_exchange_rate', 'modify_autopay', 'get_card_benefits', 'get_exchange_rate', 'modify_autopay', 'get_card_benefits']
- finance_20_66c4f3cb14cbfc4db836bd4e: Missing tools: ['modify_autopay']; Unexpected tools: ['get_exchange_rate', 'get_exchange_rate', 'get_exchange_rate']
- finance_21_5e3c1fbece3a7b000a6fd95a: Unexpected tools: ['get_card_benefits', 'get_exchange_rate', 'get_card_benefits', 'get_exchange_rate', 'get_card_benefits', 'get_exchange_rate']
- housing_01_62a885d5b6af18b3d4579e1b: Unexpected tools: ['search_apartments', 'search_apartments', 'search_apartments']
- housing_04_695bd157114f0d2317f88617: Unexpected tools: ['search_apartments']
- housing_05_5ff07b5ee7a1d23e719e421e: Unexpected tools: ['search_apartments', 'search_apartments', 'search_apartments']
- housing_05_61517db6a7589569521b2356: Unexpected tools: ['search_apartments', 'search_apartments', 'search_apartments']
- housing_06_5f4a4da1575d605c43bef871: Unexpected tools: ['calculate_commute', 'calculate_commute', 'calculate_commute']
- housing_08_66f59c766e7e22e1f90d08f6: Unexpected tools: ['search_apartments']
- housing_10_66f59c766e7e22e1f90d08f6: Unexpected tools: ['calculate_commute', 'update_search_filter', 'calculate_commute', 'update_search_filter', 'calculate_commute', 'update_search_filter']
- housing_11_62a885d5b6af18b3d4579e1b: Unexpected tools: ['search_apartments', 'search_apartments', 'search_apartments']
- housing_14_5ff07b5ee7a1d23e719e421e: Unexpected tools: ['update_search_filter', 'search_apartments', 'update_search_filter', 'search_apartments', 'update_search_filter', 'search_apartments', 'update_search_filter']
- housing_14_61517db6a7589569521b2356: Unexpected tools: ['search_apartments', 'search_apartments', 'search_apartments']
