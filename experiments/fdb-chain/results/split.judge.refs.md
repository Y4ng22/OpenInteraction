## split (LLM judge), $RESULT references resolved

| metric | value |
| --- | --- |
| pass rate (FDB-v3, strict) | 28/100 (28%) |
| tool selection F1 (mean) | 0.478 |
| argument accuracy (mean) | 0.347 |
| items with a handoff | 0/100 (0%) |
| FDB calls made by frontend / backend | 73 / 0 |
| unrelated harness tool calls | 0 |
| errors | 0 |
| frontend first token, median (s) | 2.13 |
| end to end, median (s) | 8.24 |

| difficulty | pass rate |
| --- | --- |
| easy | 10/36 (28%) |
| medium | 12/34 (35%) |
| hard | 6/30 (20%) |
| self-correction items | 5/17 (29%) |

Failures:

- ecommerce_04_6998abd731d2ec50d067d5bd: Missing tools: ['track_order']
- ecommerce_05_695bd157114f0d2317f88617: Missing tools: ['search_products']
- ecommerce_06_66c4f3cb14cbfc4db836bd4e: Missing tools: ['add_to_cart']
- ecommerce_08_5ff07b5ee7a1d23e719e421e: Missing tools: ['search_products']
- ecommerce_08_61517db6a7589569521b2356: Missing tools: ['search_products']
- ecommerce_09_695bd157114f0d2317f88617: Missing tools: ['search_products']
- ecommerce_11_62a885d5b6af18b3d4579e1b: Missing tools: ['add_to_cart']
- ecommerce_12_6998abd731d2ec50d067d5bd: Missing tools: ['search_products']
- ecommerce_13_5ff07b5ee7a1d23e719e421e: Wrong arguments for: ['add_to_cart', 'track_order']
- ecommerce_14_5ff07b5ee7a1d23e719e421e: Missing tools: ['add_to_cart']
- ecommerce_14_61517db6a7589569521b2356: Missing tools: ['add_to_cart']
- ecommerce_16_695bd157114f0d2317f88617: Missing tools: ['search_products']
- ecommerce_18_62a885d5b6af18b3d4579e1b: Missing tools: ['add_to_cart']
- ecommerce_19_66f59c766e7e22e1f90d08f6: Missing tools: ['add_to_cart']
- ecommerce_20_66c4f3cb14cbfc4db836bd4e: Missing tools: ['add_to_cart']
- ecommerce_21_65e8cf8f4c7424fa062e54a3: Missing tools: ['add_to_cart']
- ecommerce_21_69a9cf80f4d7668d5c815038: Missing tools: ['add_to_cart']
- ecommerce_25_69a9cf80f4d7668d5c815038: Missing tools: ['track_order', 'search_products', 'add_to_cart']
- finance_01_69a9cf80f4d7668d5c815038: Missing tools: ['get_exchange_rate']
- finance_03_5ff07b5ee7a1d23e719e421e: Missing tools: ['modify_autopay']
- finance_03_61517db6a7589569521b2356: Missing tools: ['modify_autopay']
- finance_04_62a885d5b6af18b3d4579e1b: Missing tools: ['get_exchange_rate']
- finance_06_66f59c766e7e22e1f90d08f6: Missing tools: ['get_card_benefits']
- finance_07_5ff07b5ee7a1d23e719e421e: Missing tools: ['modify_autopay']
- finance_07_61517db6a7589569521b2356: Missing tools: ['modify_autopay']
- finance_08_65e8cf8f4c7424fa062e54a3: Missing tools: ['get_exchange_rate']
- finance_08_69a9cf80f4d7668d5c815038: Missing tools: ['get_exchange_rate']
- finance_10_66c4f3cb14cbfc4db836bd4e: Missing tools: ['get_card_benefits', 'get_exchange_rate']
- finance_11_695bd157114f0d2317f88617: Missing tools: ['modify_autopay']
- finance_14_5f4a4da1575d605c43bef871: Missing tools: ['get_exchange_rate', 'get_exchange_rate']
- finance_15_66c4f3cb14cbfc4db836bd4e: Missing tools: ['modify_autopay']
- finance_18_6998abd731d2ec50d067d5bd: Missing tools: ['get_exchange_rate', 'modify_autopay', 'get_card_benefits']
- finance_20_66c4f3cb14cbfc4db836bd4e: Missing tools: ['modify_autopay']
- finance_22_5f4a4da1575d605c43bef871: Missing tools: ['modify_autopay', 'modify_autopay']
- finance_23_66f59c766e7e22e1f90d08f6: Missing tools: ['modify_autopay']
- housing_01_62a885d5b6af18b3d4579e1b: Missing tools: ['search_apartments']
- housing_04_695bd157114f0d2317f88617: Missing tools: ['search_apartments']
- housing_05_5ff07b5ee7a1d23e719e421e: Missing tools: ['search_apartments']
- housing_05_61517db6a7589569521b2356: Missing tools: ['search_apartments']
- housing_06_5f4a4da1575d605c43bef871: Missing tools: ['calculate_commute']
