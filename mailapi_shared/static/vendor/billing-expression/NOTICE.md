This directory contains the display-only billing parser from QuantumNous/New API,
copied from the user-provided design reference on 2026-11-21.
Upstream: https://github.com/QuantumNous/new-api/tree/main/web/src/features/pricing/lib
Copyright (C) 2023-2026 QuantumNous. License: AGPL-3.0-or-later (see LICENSE).

The original copyright notices and corresponding TypeScript sources are included
in source/. The only vendor modification replaces the condition formatter's
application-specific locale import with MILVLG's Chinese/English locale lookup.
The MILVLG presentation adapter is frontend/api-billing.ts. Rebuild the browser
bundle with `node frontend/build-api-billing.mjs` from the repository root.

This parser describes prices; the native gateway remains authoritative for actual
settlement. Unsupported expressions retain their source and do not imply a zero
or fixed unit price.
