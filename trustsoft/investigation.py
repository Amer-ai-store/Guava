"""Candidates only. Bounded samples cannot prove missing vouchers or misconduct."""
from collections import defaultdict
from decimal import Decimal, InvalidOperation

def investigate(sections):
    reports = []
    groups = defaultdict(list)
    for row in sections.get('payments', {}).get('rows', []):
        required = ('payment_id', 'party_id', 'amount', 'currency', 'reference', 'date')
        if any(row.get(k) in (None, '') for k in required):
            continue
        try:
            amount = Decimal(str(row['amount']))
            if not amount.is_finite() or amount <= 0:
                continue
        except InvalidOperation:
            continue
        key = (str(row['party_id']), amount, str(row['currency']), str(row['reference']), str(row['date'])[:10])
        groups[key].append(row)
    for rows in groups.values():
        if len({str(r['payment_id']) for r in rows}) > 1:
            reports.append({'type': 'possible_duplicate_payment', 'confidence': 'candidate', 'evidence': rows})
    for row in sections.get('audit', {}).get('rows', []):
        if row.get('event_type') in ('edit', 'unpost') and row.get('event_id') is not None:
            reports.append({'type': row['event_type'], 'confidence': 'recorded_event_requires_verification',
                'evidence': {k: row[k] for k in ('event_id','document_id','event_type','user','device','timestamp','before','after') if row.get(k) is not None}})
    return {'findings': reports, 'coverage': 'bounded_sample_only',
        'missing_vouchers': {'state': 'not_assessed', 'reason': 'Requires complete reconciled sequence per company, branch, fiscal year, document type and cancellations.'},
        'warning': 'Candidates are not proof. Validate source mappings, reversals, cancellations and ledger records in TrustSoft.'}
