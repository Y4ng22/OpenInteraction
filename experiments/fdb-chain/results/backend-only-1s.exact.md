## backend-only-1s (exact match)

| metric | value |
| --- | --- |
| pass rate (FDB-v3, strict) | 5/100 (5%) |
| tool selection F1 (mean) | 0.151 |
| argument accuracy (mean) | 0.085 |
| items with a handoff | 16/100 (16%) |
| FDB calls made by frontend / backend | 0 / 32 |
| unrelated harness tool calls | 7 |
| errors | 0 |
| frontend first token, median (s) | 1.66 |
| end to end, median (s) | 4.44 |

| difficulty | pass rate |
| --- | --- |
| easy | 2/36 (6%) |
| medium | 2/34 (6%) |
| hard | 1/30 (3%) |
| self-correction items | 2/17 (12%) |

Failures:

- ecommerce_01_65e8cf8f4c7424fa062e54a3: Missing tools: ['track_order']
- ecommerce_01_69a9cf80f4d7668d5c815038: Missing tools: ['track_order']
- ecommerce_02_5f4a4da1575d605c43bef871: Missing tools: ['search_products']
- ecommerce_04_6998abd731d2ec50d067d5bd: Missing tools: ['track_order']
- ecommerce_05_695bd157114f0d2317f88617: Missing tools: ['search_products']
- ecommerce_06_66c4f3cb14cbfc4db836bd4e: Missing tools: ['add_to_cart']
- ecommerce_08_5ff07b5ee7a1d23e719e421e: Missing tools: ['search_products']
- ecommerce_08_61517db6a7589569521b2356: Missing tools: ['search_products']
- ecommerce_09_695bd157114f0d2317f88617: Missing tools: ['search_products']
- ecommerce_11_62a885d5b6af18b3d4579e1b: Missing tools: ['add_to_cart']
- ecommerce_12_6998abd731d2ec50d067d5bd: Missing tools: ['search_products']
- ecommerce_13_5ff07b5ee7a1d23e719e421e: Missing tools: ['add_to_cart', 'track_order']
- ecommerce_13_61517db6a7589569521b2356: Missing tools: ['add_to_cart', 'track_order']
- ecommerce_14_5ff07b5ee7a1d23e719e421e: Missing tools: ['add_to_cart']
- ecommerce_14_61517db6a7589569521b2356: Missing tools: ['add_to_cart']
- ecommerce_15_56780a5a89319e0011644cb4: Missing tools: ['track_order']
- ecommerce_15_66c4f3cb14cbfc4db836bd4e: Missing tools: ['track_order']
- ecommerce_16_695bd157114f0d2317f88617: Missing tools: ['search_products']
- ecommerce_17_5f4a4da1575d605c43bef871: Missing tools: ['track_order']
- ecommerce_18_62a885d5b6af18b3d4579e1b: Missing tools: ['search_products', 'add_to_cart', 'track_order']
- ecommerce_19_66f59c766e7e22e1f90d08f6: Missing tools: ['search_products', 'add_to_cart']
- ecommerce_20_66c4f3cb14cbfc4db836bd4e: Missing tools: ['search_products', 'add_to_cart']
- ecommerce_21_65e8cf8f4c7424fa062e54a3: Missing tools: ['add_to_cart']
- ecommerce_21_69a9cf80f4d7668d5c815038: Wrong arguments for: ['track_order']
- ecommerce_23_5f4a4da1575d605c43bef871: Missing tools: ['track_order', 'add_to_cart']
- ecommerce_25_65e8cf8f4c7424fa062e54a3: Missing tools: ['track_order', 'search_products', 'add_to_cart']
- ecommerce_25_69a9cf80f4d7668d5c815038: Missing tools: ['track_order', 'search_products', 'add_to_cart']
- finance_01_65e8cf8f4c7424fa062e54a3: Missing tools: ['get_exchange_rate']
- finance_01_69a9cf80f4d7668d5c815038: Missing tools: ['get_exchange_rate']
- finance_02_6998abd731d2ec50d067d5bd: Missing tools: ['get_card_benefits']
- finance_03_5ff07b5ee7a1d23e719e421e: Missing tools: ['modify_autopay']
- finance_03_61517db6a7589569521b2356: Missing tools: ['modify_autopay']
- finance_06_66f59c766e7e22e1f90d08f6: Missing tools: ['get_card_benefits']
- finance_07_5ff07b5ee7a1d23e719e421e: Missing tools: ['modify_autopay']
- finance_07_61517db6a7589569521b2356: Missing tools: ['modify_autopay']
- finance_08_65e8cf8f4c7424fa062e54a3: Missing tools: ['get_exchange_rate']
- finance_08_69a9cf80f4d7668d5c815038: Missing tools: ['get_exchange_rate']
- finance_10_66c4f3cb14cbfc4db836bd4e: Missing tools: ['get_card_benefits', 'get_exchange_rate']
- finance_11_695bd157114f0d2317f88617: Missing tools: ['modify_autopay']
- finance_12_69a9cf80f4d7668d5c815038: Missing tools: ['get_exchange_rate']
