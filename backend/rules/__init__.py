from .circular_reference import CircularReferenceRule
from .type_mismatch import TypeMismatchRule
from .overwrite_referenced_cell import OverwriteReferencedCellRule
from .date_format_check import DateFormatCheckRule
from .email_format_check import EmailFormatCheckRule
from .percentage_range_check import PercentageRangeCheckRule
from .forecast_sanity import ForecastSanityRule
from .forecast_integrity import ForecastIntegrityRule
from .clean_integrity import CleanIntegrityRule
from .financial import (
    DcfSanityRule,
    DcfIntegrityRule,
    TgrVsWaccRule,
    WaccRangeRule,
    WaccHardcodedRule,
    TaxRateRangeRule,
    TaxRateReasonableRule,
    HorizontalFormulaConsistencyRule,
    HardcodeTrendAnomalyRule,
    DebtEquitySumRule,
    BetaRangeRule,
    TgrVsGdpRule,
)

RULES = [
    # Type 1: structural checks
    CircularReferenceRule(),
    TypeMismatchRule(),
    OverwriteReferencedCellRule(),
    # Type 2: header-convention checks
    DateFormatCheckRule(),
    EmailFormatCheckRule(),
    PercentageRangeCheckRule(),
    # Type 3: forecast acceptance-range check
    ForecastSanityRule(),
    # Type 4: forecast declared-vs-actual integrity (history provenance,
    # rate reconciliation, method guardrail)
    ForecastIntegrityRule(),
    # Type 5: cleaning declared-vs-actual integrity (old-value provenance,
    # transform correctness) — apply_cleaning only
    CleanIntegrityRule(),
    # Type 6: DCF — numeric sanity on the declared assumptions, and
    # declared-vs-actual integrity of the emitted two-sheet template
    # (apply_dcf_template only)
    DcfSanityRule(),
    DcfIntegrityRule(),
]

RULES_REVIEW = [
    TgrVsWaccRule(),
    WaccRangeRule(),
    WaccHardcodedRule(),
    TaxRateRangeRule(),
    TaxRateReasonableRule(),
    HorizontalFormulaConsistencyRule(),
    HardcodeTrendAnomalyRule(),
    DebtEquitySumRule(),
    BetaRangeRule(),
    TgrVsGdpRule(),
]
