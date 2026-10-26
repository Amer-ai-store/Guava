"""One authorized read-only connection and metadata probe, no file exports."""
import json
from trustsoft.source import SqlSource, SourceError, encode

try:
    result = SqlSource().snapshot()
    print(json.dumps({'state':'verified','identity':result['identity'],
                      'catalog_columns':len(result['catalog']),'foreign_key_columns':len(result['relations'])},default=encode))
except SourceError as exc:
    print(json.dumps({'state':'blocked','reason':str(exc)}))
    raise SystemExit(1)
