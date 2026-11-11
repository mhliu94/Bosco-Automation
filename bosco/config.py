from dataclasses import dataclass
from pathlib import Path
import copy
import json
import os
import math
from .defaults import DEFAULTS
from .errors import ConfigurationError

def _merge(base, override):
    result = copy.deepcopy(base)
    for k, v in override.items():
        result[k] = _merge(result[k], v) if isinstance(v, dict) and isinstance(result.get(k), dict) else v
    return result

def project_root():
    return Path(__file__).resolve().parent.parent

@dataclass(frozen=True)
class Settings:
    data: dict
    path: Path
    root: Path

    def get(self, dotted, default=None):
        current = self.data
        for key in dotted.split('.'):
            if not isinstance(current, dict) or key not in current:
                return default
            current = current[key]
        return current

    def require(self, dotted):
        value = self.get(dotted)
        if value is None or value == '':
            raise ConfigurationError(f'Required setting is empty: {dotted}')
        return value

    def resolve_path(self, value):
        path = Path(value).expanduser()
        return (path if path.is_absolute() else self.root / path).resolve()

    @property
    def work_dir(self):
        path = self.resolve_path(self.get('paths.workDir', 'work'))
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        return path

    @property
    def log_dir(self):
        return self.resolve_path(self.get('paths.logDir', 'logs'))

    @property
    def session_path(self):
        value = self.get('api.sessionPath')
        return self.resolve_path(value) if value else self.work_dir / 'api/session.json'

    @property
    def kafka_client_config(self):
        config = dict(self.get('kafka.clientConfig', {}))
        for k in ('ssl.ca.location','ssl.certificate.location','ssl.key.location','sasl.kerberos.keytab'):
            if config.get(k):
                config[k] = str(self.resolve_path(config[k]))
        return config

def execution_backend(settings):
    if settings.get('bosco.backend', 'api') != 'api':
        raise ConfigurationError('Only the direct API backend is supported.')
    return 'api'

def load_settings(path=None):
    path = Path(path or os.environ.get('BOSCO_CONFIG', project_root()/'config/bosco.config.json')).expanduser().resolve()
    try:
        raw = json.loads(path.read_text('utf-8-sig'))
    except (OSError, ValueError):
        raise ConfigurationError(f'Cannot read configuration: {path}') from None
    if not isinstance(raw, dict):
        raise ConfigurationError('Configuration must be a JSON object.')
    data = _merge(DEFAULTS, raw)
    def validate(obj, defaults, prefix=''):
        for k,v in obj.items():
            name = prefix+k
            if k not in defaults:
                raise ConfigurationError(f'Unknown setting: {name}')
            expected = defaults[k]
            if isinstance(expected, dict):
                if not isinstance(v,dict): raise ConfigurationError(f'{name} must be an object.')
                if name != 'kafka.clientConfig': validate(v,expected,name+'.')
            elif isinstance(expected,bool):
                if type(v) is not bool: raise ConfigurationError(f'{name} must be boolean.')
            elif isinstance(expected,(int,float)):
                if type(v) not in (int,float) or not math.isfinite(v) or v<0: raise ConfigurationError(f'{name} must be finite and nonnegative.')
            elif expected is not None and not isinstance(v,type(expected)):
                raise ConfigurationError(f'Invalid setting type: {name}')
    validate(raw, DEFAULTS)
    numeric = data['bosco']['accountNumId']
    if numeric is not None and (type(numeric) is not int or numeric<=0): raise ConfigurationError('accountNumId must be a positive integer.')
    # Relative paths travel with the exported account folder, independent of CWD.
    settings = Settings(data, path, path.parent)
    execution_backend(settings)
    for name in ('api.readAttempts', 'api.pageSize', 'api.maxOrderPages', 'api.maxResponseBytes'):
        value = settings.get(name)
        if type(value) is not int or value < 1:
            raise ConfigurationError(f'{name} must be a positive integer.')
    if settings.get('api.requestTimeoutSeconds') <= 0:
        raise ConfigurationError('api.requestTimeoutSeconds must be positive.')
    if settings.get('api.positionCost') not in ('diluted', 'average'):
        raise ConfigurationError('api.positionCost must be diluted or average.')
    delays = settings.get('api.cancelPollDelaysSeconds')
    if not isinstance(delays, list) or not delays or any(type(x) not in (int,float) or not math.isfinite(x) or x<0 for x in delays):
        raise ConfigurationError('api.cancelPollDelaysSeconds must contain nonnegative finite delays.')
    return settings
