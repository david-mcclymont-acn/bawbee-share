"""Demo Bank: a fake provider with synthetic data, for CI and Codespaces only.

On only when BAWBEE_BANKS_DEMO=true. It goes through the same link, sync and consent code as
the real banks. Its payments are made up but shaped like the household's: a salary, Sky, the
mortgage, the weekly shop. Each day's rows are the same every time they're asked for, and
anything from the last two days is pending under a different id, so the pending-to-booked
path gets exercised.
"""

from datetime import UTC, date, datetime, timedelta

from app.providers.base import ProviderAccount, ProviderBalance, ProviderTransaction

BANK_NAME = "Demo Bank"
CURRENT = ProviderAccount(
    uid="demo-current", name="Demo Current", identification_hash="demo:current", detail="…0001"
)
JOINT = ProviderAccount(
    uid="demo-joint", name="Demo Joint", identification_hash="demo:joint", detail="Joint …0002"
)
PENDING_DAYS = 2


def _payments(account: ProviderAccount, day: date) -> list[tuple[str, int]]:
    """(text, pence) paid on `day` from this account."""
    out: list[tuple[str, int]] = []
    if account.uid == CURRENT.uid:
        if day.day == 28:
            out.append(("DEMO EMPLOYER LTD SALARY", 300000))
        if day.weekday() == 4:
            out.append(("COSTA COFFEE", -385))
    else:
        if day.day == 1:
            out += [("SKY DIGITAL", -10475), ("NORTHFIELD B S MORTGAGE", -119246)]
        if day.weekday() == 5:
            out.append(("TESCO STORES", -8412))
    return out


class DemoProvider:
    def __init__(self, today: date | None = None) -> None:
        self.today = today or datetime.now(UTC).date()

    def list_accounts(self) -> list[ProviderAccount]:
        return [CURRENT, JOINT]

    def fetch_transactions(
        self, account: ProviderAccount, since: date | None, until: date
    ) -> list[ProviderTransaction]:
        start = since or until - timedelta(days=90)
        out = []
        day = start
        while day <= min(until, self.today):
            pending = (self.today - day).days < PENDING_DAYS
            for n, (text, pence) in enumerate(_payments(account, day)):
                prefix = "demo-p" if pending else "demo"
                out.append(
                    ProviderTransaction(
                        external_id=f"{prefix}:{account.uid}:{day.isoformat()}:{n}",
                        booked_date=day,
                        amount_pence=pence,
                        description=text,
                        counterparty=text.title(),
                        status="pending" if pending else "booked",
                    )
                )
            day += timedelta(days=1)
        return out

    def fetch_balance(self, account: ProviderAccount) -> ProviderBalance | None:
        return ProviderBalance(250000 if account.uid == CURRENT.uid else 412500, datetime.now(UTC))
