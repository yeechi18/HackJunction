"""Provider selection is explicit: a failed live configuration never falls back to fixtures."""

from company.config import Settings
from company.providers.fake import FakeProvider
from company.providers.mimo import MiMoProvider, ProviderError


def build_provider(settings: Settings) -> MiMoProvider | FakeProvider:
    if settings.mimo_mode == "fake":
        if settings.app_env != "test":
            raise ProviderError("Offline fixtures require APP_ENV=test and MIMO_MODE=fake.")
        return FakeProvider(settings)
    if settings.mimo_mode != "live":
        raise ProviderError("Unsupported model provider mode.")
    return MiMoProvider(settings)
