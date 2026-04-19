import secrets
from collections.abc import Callable

from django.apps import apps
from django.utils import timezone

ACCOUNT_NO_RANDOM_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def generate_payment_account_no(
    *,
    source: str,
    exists: Callable[[str], bool],
    account_date=None,
    prefix: str = "",
    token_length: int = 8,
    max_attempts: int = 10,
) -> str:
    account_date = account_date or timezone.now()
    local_date = account_date.astimezone(timezone.get_current_timezone())

    for _attempt in range(max_attempts):
        token = "".join(secrets.choice(ACCOUNT_NO_RANDOM_ALPHABET) for _index in range(token_length))
        account_no = f"{prefix}{source}{local_date:%d%m%y}{token}"

        if not exists(account_no):
            return account_no

    msg = "Unable to generate a unique payment account number."
    raise RuntimeError(msg)


def payment_account_no_exists(account_no: str, *, model_labels: tuple[str, ...]) -> bool:
    for model_label in model_labels:
        model = apps.get_model(model_label)
        if model.objects.filter(payment_account_no=account_no).exists():
            return True
    return False
