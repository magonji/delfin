"""
Amortisation engine: turns the agreed terms of a loan into its schedule.

Until now a loan's rate and progress were *estimated* from the movements it left
behind — an XIRR over the actual cash flows. That is the best you can do with no
contract, but it drifts: an early overpayment or a fee booked to the wrong
category moves the estimate. Given the real terms, the schedule is arithmetic.

Two rhythms, not one. Interest is charged every ``interest_months`` and an
instalment is paid every ``payment_months``. They usually match. When they don't
— interest charged monthly on a loan repaid quarterly — the interest accrued
between instalments compounds into the balance, which is what the rate per
payment period below expresses.

Interest can also accrue **daily**, which most mortgages do, and that is a
different shape rather than a shorter period: the interest in each instalment
follows the actual days it covers, so a February one carries less than a March
one. The instalment itself stays level, because the lender fixes it from the
average period — the difference lands where every other rounding does, in the
final payment. Days are counted ACT/365F: real days over a fixed 365-day year,
so a leap year genuinely costs one day more.

An arrangement fee is a cost of the loan but not interest, so it stays out of the
nominal rate and shows up in two other places instead: in the capital amortised,
when it is added to the debt rather than paid at the outset, and always in the
effective rate — the one that answers "what is this borrowing really costing me",
and the only figure on which two offers can honestly be compared. A standing
administration fee is the same kind of cost, spread out, and counts the same way.

An early repayment charge is different in kind, and is deliberately kept out of
both the schedule and the effective rate: it is the price of a decision not yet
taken. It prices one thing only — settling the loan today.

The schedule is *theoretical*: it says what the contract implies, never what was
actually paid. Comparing the two is the caller's job, and the point of having it.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime
from typing import Dict, List, Optional

from backend.budget_engine import as_date, day_of_month_for

REPAYMENT_TYPES = ("french", "interest_only", "constant_principal")
TERM_UNITS = ("month", "year")
FEE_TREATMENTS = ("upfront", "capitalised")
INTEREST_UNITS = ("month", "day")
# Months between charges, offered as a frequency. 1 = monthly … 12 = annual.
FREQUENCY_MONTHS = (1, 3, 6, 12)
# ACT/365F: interest accrues over the real days of a period, but a year is
# always 365 days — the convention British lenders quote daily rates on.
DAYS_PER_YEAR = 365.0


def add_months(d: date, months: int) -> date:
    """`d` shifted by whole months, clamped to the length of the target month."""
    total = (d.year * 12 + d.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    return date(year, month, min(d.day, monthrange(year, month)[1]))


def term_months(loan) -> int:
    """The duration of the loan in months."""
    count = max(1, int(loan.term_count or 1))
    return count * (12 if (loan.term_unit or "year") == "year" else 1)


def payment_count(loan) -> int:
    """How many instalments the loan is repaid in."""
    every = max(1, int(loan.payment_months or 1))
    return max(1, term_months(loan) // every)


def opening_fee(loan) -> float:
    """The arrangement fee, never negative."""
    return max(0.0, round(float(getattr(loan, "opening_fee", 0) or 0), 2))


def financed_principal(loan) -> float:
    """
    The capital the schedule actually amortises. A capitalised fee is borrowed
    too — it is repaid, with interest, over the life of the loan.
    """
    principal = float(loan.principal or 0)
    if (getattr(loan, "fee_treatment", None) or "upfront") == "capitalised":
        return round(principal + opening_fee(loan), 2)
    return round(principal, 2)


def net_advanced(loan) -> float:
    """
    The money that actually reaches the borrower. A fee paid at the outset comes
    straight back out of it, which is exactly why it makes the loan dearer than
    its nominal rate suggests.
    """
    principal = float(loan.principal or 0)
    if (getattr(loan, "fee_treatment", None) or "upfront") == "capitalised":
        return round(principal, 2)
    return round(principal - opening_fee(loan), 2)


def recurring_fee(loan) -> float:
    """The standing administration fee, never negative."""
    return max(0.0, round(float(getattr(loan, "recurring_fee", 0) or 0), 2))


def fees_in_period(loan, i: int) -> int:
    """
    How many standing fees fall between instalment ``i-1`` and instalment ``i``.

    Fees are charged every ``recurring_fee_months`` from the opening date, on
    their own rhythm. Counting how many of those months have gone by at each
    instalment, and differencing, lands every fee in exactly one period — whether
    they come round faster than the instalments or slower.
    """
    every = max(1, int(getattr(loan, "recurring_fee_months", 1) or 1))
    pay = max(1, int(loan.payment_months or 1))
    return (i * pay) // every - ((i - 1) * pay) // every


def early_repayment_fee_pct(loan) -> float:
    """The early settlement charge, as a percentage of the capital outstanding."""
    return max(0.0, float(getattr(loan, "early_repayment_fee_pct", 0) or 0))


def accrues_daily(loan) -> bool:
    """Whether interest follows the days rather than the months."""
    return (getattr(loan, "interest_unit", None) or "month") == "day"


def daily_rate(loan) -> float:
    """The rate a single day earns, as a decimal."""
    return max(0.0, float(loan.annual_rate or 0) / 100.0 / DAYS_PER_YEAR)


def rate_over_days(loan, days: float) -> float:
    """
    What a stretch of `days` earns at the daily rate, compounded — the amount by
    which the debt grows between one instalment and the next.
    """
    d = daily_rate(loan)
    if d <= 0 or days <= 0:
        return 0.0
    return (1.0 + d) ** days - 1.0


def rate_per_payment(loan) -> float:
    """
    The interest rate applied at each instalment, as a decimal.

    ``annual_rate`` is nominal: an interest period earns its pro-rata slice of
    it. Raising that to the number of interest periods per instalment compounds
    the ones that pass between instalments — and reduces to the plain slice when
    the two rhythms coincide.

    With daily accrual this is the *average* period, the one an average month
    long. It is what fixes the level instalment; the schedule then charges each
    period its real days, which is where the two part company.
    """
    annual = float(loan.annual_rate or 0) / 100.0
    if annual <= 0:
        return 0.0
    pay_every = max(1, int(loan.payment_months or 1))
    if accrues_daily(loan):
        return rate_over_days(loan, DAYS_PER_YEAR * pay_every / 12.0)
    interest_every = max(1, int(loan.interest_months or 1))
    per_interest_period = annual * interest_every / 12.0
    return (1.0 + per_interest_period) ** (pay_every / interest_every) - 1.0


def instalment(loan) -> float:
    """
    The instalment the terms imply. For a falling instalment
    (``constant_principal``) this is the first one — the largest.
    """
    principal = financed_principal(loan)
    n = payment_count(loan)
    r = rate_per_payment(loan)
    kind = loan.repayment_type or "french"

    if kind == "interest_only":
        return round(principal * r, 2)
    if kind == "constant_principal":
        return round(principal / n + principal * r, 2)
    # French: the constant instalment that clears the capital in n periods.
    if r == 0:
        return round(principal / n, 2)
    return round(principal * r / (1.0 - (1.0 + r) ** -n), 2)


def payment_dates(loan) -> List[date]:
    """
    When each instalment falls due. The first lands one payment period after the
    loan is drawn down; the day of the month comes from the loan's day rule, so
    "the first working day" resolves afresh in every month.
    """
    opened = as_date(loan.open_date)
    if not opened:
        return []
    every = max(1, int(loan.payment_months or 1))
    fallback = int(loan.day_of_month or opened.day)
    out = []
    for i in range(1, payment_count(loan) + 1):
        landing = add_months(opened, i * every)
        day = day_of_month_for(loan, landing.year, landing.month, fallback)
        out.append(date(landing.year, landing.month, day))
    return out


def schedule(loan) -> List[Dict]:
    """
    The full amortisation table, one row per instalment.

    The final row absorbs the rounding of every one before it, so the closing
    balance is exactly zero rather than a few pence adrift.
    """
    principal = financed_principal(loan)
    if principal <= 0:
        return []

    dates = payment_dates(loan)
    n = len(dates)
    if not n:
        return []

    r = rate_per_payment(loan)
    kind = loan.repayment_type or "french"
    fixed_instalment = instalment(loan) if kind == "french" else None
    capital_slice = principal / n if kind == "constant_principal" else None
    # Daily accrual charges each period its own days; the first runs from the
    # drawdown, so a loan taken out mid-month opens with a short one.
    daily = accrues_daily(loan)
    previous = as_date(loan.open_date)

    rows: List[Dict] = []
    balance = principal
    for i, due in enumerate(dates, start=1):
        opening = balance
        if daily:
            days = (due - previous).days
            interest = round(opening * rate_over_days(loan, days), 2)
            previous = due
        else:
            days = None
            interest = round(opening * r, 2)
        last = i == n

        if kind == "interest_only":
            capital = round(opening, 2) if last else 0.0
        elif kind == "constant_principal":
            capital = round(opening, 2) if last else round(capital_slice, 2)
        else:
            capital = round(opening, 2) if last else round(fixed_instalment - interest, 2)
            # A rate high enough that the instalment does not cover the interest
            # would grow the debt for ever; never amortise backwards.
            capital = max(0.0, min(capital, round(opening, 2)))

        balance = round(opening - capital, 2)
        # The standing fee rides alongside the instalment without touching the
        # capital: it buys nothing back, it is simply what the loan charges.
        fee = round(recurring_fee(loan) * fees_in_period(loan, i), 2)
        rows.append({
            "number": i,
            "date": due.isoformat(),
            "opening_balance": round(opening, 2),
            "interest": interest,
            "capital": capital,
            "fee": fee,
            "instalment": round(interest + capital, 2),
            "outflow": round(interest + capital + fee, 2),
            "closing_balance": balance,
            "days": days,  # None unless interest accrues daily
        })
    return rows


def effective_rate(loan) -> Optional[float]:
    """
    The annual rate that actually values the loan, as a percentage — the APR, or
    TAE. It is the rate at which the money advanced equals the present value of
    every instalment, so an arrangement fee raises it while the nominal rate
    stays put. Without a fee it comes back as the nominal rate compounded, which
    is the honest annual equivalent of a rate quoted per month.

    Solved by bisection rather than Newton-Raphson: the function is monotonic
    over the bracket, so bisection cannot diverge and needs no starting guess.
    Returns None when no rate values the cash flows — a fee larger than the
    money advanced, say.
    """
    rows = schedule(loan)
    advanced = net_advanced(loan)
    if not rows or advanced <= 0:
        return None

    # Every pound that leaves, fees included — that is what "really costing me" means.
    flows = [r["outflow"] for r in rows]
    periods_per_year = 12.0 / max(1, int(loan.payment_months or 1))

    def present_value(rate: float) -> float:
        """What the instalments are worth today at `rate`, less what was advanced."""
        return sum(f / (1.0 + rate) ** i for i, f in enumerate(flows, start=1)) - advanced

    low, high = 0.0, 1.0  # per payment period; 100% a period is a generous ceiling
    if present_value(low) < 0:
        return None  # the instalments never repay the advance, at any rate
    while present_value(high) > 0:
        high *= 2
        if high > 1e6:
            return None

    for _ in range(200):
        mid = (low + high) / 2
        if present_value(mid) > 0:
            low = mid
        else:
            high = mid

    per_period = (low + high) / 2
    return round(((1.0 + per_period) ** periods_per_year - 1.0) * 100, 3)


def summary(loan, today: Optional[date] = None) -> Dict:
    """
    What the contract says about the loan right now: the instalment, the next one
    due, and where the outstanding capital should stand today.
    """
    rows = schedule(loan)
    if not rows:
        return {}

    today = today or date.today()
    paid = [r for r in rows if date.fromisoformat(r["date"]) <= today]
    upcoming = [r for r in rows if date.fromisoformat(r["date"]) > today]
    total_interest = round(sum(r["interest"] for r in rows), 2)
    total_fees = round(sum(r["fee"] for r in rows), 2)

    # What it would take to be rid of the loan today: the capital still owed,
    # plus the lender's charge for ending it before the term.
    expected_balance = paid[-1]["closing_balance"] if paid else financed_principal(loan)
    settlement_fee = round(expected_balance * early_repayment_fee_pct(loan) / 100.0, 2)

    return {
        "instalment": instalment(loan),
        "payments_total": len(rows),
        "payments_made": len(paid),
        "first_payment_date": rows[0]["date"],
        "final_payment_date": rows[-1]["date"],
        "next_payment_date": upcoming[0]["date"] if upcoming else None,
        "next_payment_amount": upcoming[0]["outflow"] if upcoming else None,
        # Where the capital should stand today if every instalment was paid on time.
        "expected_balance": expected_balance,
        "total_interest": total_interest,
        "interest_paid": round(sum(r["interest"] for r in paid), 2),
        "interest_remaining": round(sum(r["interest"] for r in upcoming), 2),
        "opening_fee": opening_fee(loan),
        "fee_treatment": getattr(loan, "fee_treatment", None) or "upfront",
        "recurring_fee": recurring_fee(loan),
        "recurring_fee_months": max(1, int(getattr(loan, "recurring_fee_months", 1) or 1)),
        "total_fees": total_fees,
        "fees_remaining": round(sum(r["fee"] for r in upcoming), 2),
        "early_repayment_fee_pct": early_repayment_fee_pct(loan),
        "early_repayment_fee": settlement_fee,
        "settlement_today": round(expected_balance + settlement_fee, 2),
        "interest_unit": getattr(loan, "interest_unit", None) or "month",
        "financed_principal": financed_principal(loan),
        "net_advanced": net_advanced(loan),
        # Everything the borrower parts with. A capitalised fee is already inside
        # the financed capital; one paid at the outset has to be added on.
        "total_cost": round(
            financed_principal(loan) + total_interest + total_fees
            + (opening_fee(loan) if (getattr(loan, "fee_treatment", None) or "upfront") == "upfront" else 0.0),
            2,
        ),
        "effective_rate": effective_rate(loan),
        "rate_per_payment": round(rate_per_payment(loan) * 100, 4),
    }


def as_dict(loan) -> Dict:
    """The stored terms, in the shape the API and the page expect."""
    open_date = loan.open_date
    return {
        "id": loan.id,
        "account_id": loan.account_id,
        "name": loan.name,
        "principal": round(float(loan.principal or 0), 2),
        "currency": loan.currency,
        "annual_rate": loan.annual_rate,
        "opening_fee": opening_fee(loan),
        "fee_treatment": getattr(loan, "fee_treatment", None) or "upfront",
        "recurring_fee": recurring_fee(loan),
        "recurring_fee_months": max(1, int(getattr(loan, "recurring_fee_months", 1) or 1)),
        "early_repayment_fee_pct": early_repayment_fee_pct(loan),
        "open_date": open_date.isoformat() if isinstance(open_date, (date, datetime)) else str(open_date),
        "term_count": loan.term_count,
        "term_unit": loan.term_unit,
        "repayment_type": loan.repayment_type,
        "interest_months": loan.interest_months,
        "interest_unit": getattr(loan, "interest_unit", None) or "month",
        "payment_months": loan.payment_months,
        "day_rule": loan.day_rule,
        "day_ordinal": loan.day_ordinal,
        "day_of_month": loan.day_of_month,
        "lender_payee_id": loan.lender_payee_id,
        "lender_name": loan.lender.name if loan.lender else None,
        "disbursement_account_id": loan.disbursement_account_id,
    }
