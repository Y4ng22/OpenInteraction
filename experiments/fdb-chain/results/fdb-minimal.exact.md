## fdb-minimal (exact match)

| metric | value |
| --- | --- |
| pass rate (FDB-v3, strict) | 16/100 (16%) |
| tool selection F1 (mean) | 0.341 |
| argument accuracy (mean) | 0.198 |
| items with a handoff | 35/100 (35%) |
| FDB calls made by frontend / backend | 0 / 55 |
| unrelated harness tool calls | 18 |
| errors | 0 |
| frontend first token, median (s) | 0.44 |
| end to end, median (s) | 4.07 |

| difficulty | pass rate |
| --- | --- |
| easy | 8/36 (22%) |
| medium | 5/34 (15%) |
| hard | 3/30 (10%) |
| self-correction items | 3/17 (18%) |

Failures:

- ecommerce_01_65e8cf8f4c7424fa062e54a3: Missing tools: ['track_order']
- ecommerce_01_69a9cf80f4d7668d5c815038: Missing tools: ['track_order']
- ecommerce_04_6998abd731d2ec50d067d5bd: Wrong arguments for: ['track_order']
- ecommerce_06_66c4f3cb14cbfc4db836bd4e: Missing tools: ['add_to_cart']
- ecommerce_08_5ff07b5ee7a1d23e719e421e: Missing tools: ['search_products']
- ecommerce_08_61517db6a7589569521b2356: Missing tools: ['search_products']
- ecommerce_10_6998abd731d2ec50d067d5bd: Missing tools: ['track_order', 'search_products']
- ecommerce_11_62a885d5b6af18b3d4579e1b: Missing tools: ['add_to_cart']
- ecommerce_12_6998abd731d2ec50d067d5bd: Missing tools: ['search_products']
- ecommerce_13_5ff07b5ee7a1d23e719e421e: Missing tools: ['add_to_cart', 'track_order']
- ecommerce_13_61517db6a7589569521b2356: Missing tools: ['add_to_cart', 'track_order']
- ecommerce_14_5ff07b5ee7a1d23e719e421e: Missing tools: ['add_to_cart']
- ecommerce_15_56780a5a89319e0011644cb4: Missing tools: ['track_order']
- ecommerce_15_66c4f3cb14cbfc4db836bd4e: Missing tools: ['track_order']
- ecommerce_18_62a885d5b6af18b3d4579e1b: Wrong arguments for: ['search_products']
- ecommerce_19_66f59c766e7e22e1f90d08f6: Missing tools: ['search_products', 'add_to_cart']
- ecommerce_20_66c4f3cb14cbfc4db836bd4e: Missing tools: ['search_products', 'add_to_cart']
- ecommerce_21_65e8cf8f4c7424fa062e54a3: Missing tools: ['track_order', 'search_products', 'add_to_cart']
- ecommerce_21_69a9cf80f4d7668d5c815038: Wrong arguments for: ['track_order']
- ecommerce_25_69a9cf80f4d7668d5c815038: Missing tools: ['add_to_cart']
- finance_02_6998abd731d2ec50d067d5bd: Missing tools: ['get_card_benefits']
- finance_03_5ff07b5ee7a1d23e719e421e: Missing tools: ['modify_autopay']
- finance_04_62a885d5b6af18b3d4579e1b: Unexpected tools: ['get_exchange_rate', 'get_exchange_rate']
- finance_06_66f59c766e7e22e1f90d08f6: Missing tools: ['get_card_benefits']
- finance_07_5ff07b5ee7a1d23e719e421e: Missing tools: ['modify_autopay']
- finance_07_61517db6a7589569521b2356: Missing tools: ['modify_autopay']
- finance_08_65e8cf8f4c7424fa062e54a3: Missing tools: ['get_exchange_rate']
- finance_08_69a9cf80f4d7668d5c815038: Missing tools: ['get_exchange_rate']
- finance_10_66c4f3cb14cbfc4db836bd4e: Missing tools: ['get_card_benefits', 'get_exchange_rate']
- finance_11_695bd157114f0d2317f88617: Missing tools: ['modify_autopay']
- finance_12_65e8cf8f4c7424fa062e54a3: Missing tools: ['get_exchange_rate']
- finance_12_69a9cf80f4d7668d5c815038: Missing tools: ['get_exchange_rate']
- finance_14_5f4a4da1575d605c43bef871: Missing tools: ['get_exchange_rate', 'get_exchange_rate']
- finance_15_66c4f3cb14cbfc4db836bd4e: Missing tools: ['modify_autopay']
- finance_16_62a885d5b6af18b3d4579e1b: Missing tools: ['get_exchange_rate']
- finance_17_6998abd731d2ec50d067d5bd: Missing tools: ['get_card_benefits']
- finance_18_6998abd731d2ec50d067d5bd: Missing tools: ['get_exchange_rate', 'modify_autopay', 'get_card_benefits']
- finance_19_66f59c766e7e22e1f90d08f6: Missing tools: ['get_exchange_rate']
- finance_20_66c4f3cb14cbfc4db836bd4e: Missing tools: ['get_exchange_rate', 'modify_autopay']
- finance_21_5e3c1fbece3a7b000a6fd95a: Missing tools: ['get_card_benefits', 'get_exchange_rate']
