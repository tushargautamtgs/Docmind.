"""Validate exact provenance and semantic roles, then construct fixed formulas."""
import re
from decimal import Decimal


class CalculationFailure(ValueError):
    def __init__(self, status, detail):
        self.status = status
        super().__init__(detail)


WORDS = dict(zip(
    'zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split(),
    range(20),
))
WORDS.update(dict(zip('twenty thirty forty fifty sixty seventy eighty ninety'.split(), range(20, 100, 10))))
WORDS.update({'one hundred': 100, 'one thousand': 1000})


def normalize_number(token):
    token = token.casefold()
    if token in WORDS:
        return Decimal(WORDS[token])
    if not re.fullmatch(r'(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)(?:\.\d+)?', token):
        raise CalculationFailure('invalid_model_response', 'Unsupported numeric form.')
    value = Decimal(token.replace(',', ''))
    if value > Decimal('1e12'):
        raise CalculationFailure('invalid_model_response', 'Number outside supported range.')
    return value


def normalize_text(text):
    return ' '.join(text.split())


UNITS = {'days': r'\b(?:days?|(?:sick|annual) leaves?)\b', 'INR': r'₹|\b(?:INR|Rs\.?|rupees?)\b',
         'USD': r'\$|\bUSD\b', 'EUR': r'€|\bEUR\b'}
PERIODS = {'annual': r'\b(?:annual|annually|year|yearly)\b',
           'monthly': r'\b(?:month|monthly)\b', 'weekly': r'\b(?:week|weekly)\b',
           'per_night': r'\b(?:per night|one (?:approved )?night|1 (?:approved )?night)\b',
           'per_day': r'\b(?:per day|one (?:approved )?day|1 (?:approved )?day)\b',
           'whole_trip': r'\b(?:whole trip|entire trip|total trip)\b',
           'one_time': r'\b(?:one.time|single)\b'}


def fail(detail, status='invalid_model_response'):
    raise CalculationFailure(status, detail)


def verify(item, source, role, question, matches, category=None):
    if item.source != source:
        fail('Incorrect input source.')
    quote = normalize_text(item.quote)
    candidates = [m for m in matches if quote in normalize_text(m['text'])]
    if source == 'document':
        if not candidates:
            fail('Document quote does not match retrieved evidence.')
        # Resolve provenance from actual text, including duplicate overlapping chunks.
        resolved = next((m for m in candidates if m['chunk_id'] == item.chunk_id), candidates[0])
    else:
        if item.chunk_id is not None or quote not in normalize_text(question):
            fail('Question quote does not match.')
        resolved = None
    token = item.number_text
    pattern = r'(?<![\w.,-])' + re.escape(token) + r'(?![\w.,-])'
    hits = list(re.finditer(pattern, quote, re.I))
    if len(hits) != 1 or normalize_number(token) != normalize_number(item.value):
        fail('Value is not supported by its number token.')
    # Limit role checks to the local clause containing the selected number.
    start, end = hits[0].span()
    clause_start = max(quote.rfind(';', 0, start), quote.rfind('\n', 0, start)) + 1
    clause_end = quote.find(';', end)
    clause = quote[clause_start:clause_end if clause_end >= 0 else len(quote)]
    roles = {'entitlement': r'\b(?:entitled|entitlement|allowance|receive|provides?|granted)\b',
             'used': r'\b(?:used|taken|spent)\b',
             'cap': r'\b(?:cap|maximum|up to|limit|capped)\b',
             'actual': r'\b(?:actual|spent|paid|cost)\b'}
    periodic_leave = (
        role == 'entitlement' and item.unit == 'days'
        and re.search(re.escape(token) + r'\s+(?:paid\s+)?(?:sick\s+|annual\s+)?leave\s+days?\s+per\s+(?:calendar\s+)?year', clause, re.I)
        and not re.search(r'\b(?:used|taken|remaining|carried)\b', clause, re.I)
    )
    if not re.search(roles[role], clause, re.I) and not periodic_leave:
        fail('Amount role is not supported by its quote.')
    if role == 'entitlement':
        for leave_type in ('sick', 'annual'):
            if re.search(r'\b' + leave_type + r'\s+(?:leave|days)', question, re.I) and not re.search(r'\b' + leave_type + r'\b', clause, re.I):
                fail('Leave category is not supported by its quote.')
    if category and not re.search(r'\b' + re.escape(category) + r'\b', clause, re.I):
        fail('Category is not supported by its quote.')
    if not re.search(UNITS[item.unit], clause, re.I):
        fail('Unit is not supported by its quote.')
    if not re.search(PERIODS[item.period], clause, re.I):
        fail('Time period is not supported by its quote.')
    # A broad quote containing different numbers cannot establish which has this role.
    # Require the extracted number to be the sole amount (one day/night is a period qualifier).
    word_atoms = sorted(set(WORDS) | {'hundred', 'thousand', 'million', 'billion'}, key=len, reverse=True)
    atom = '(?:' + '|'.join(map(re.escape, word_atoms)) + ')'
    amount_clause = re.sub(r'\b(?:one|1) approved (?:day|night)\b', '', clause, flags=re.I)
    amounts = re.findall(r'(?<![\w.])\d[\d,.]*(?:[eE][+-]?\d+)?(?![\w.])|' +
                         r'\b' + atom + r'(?:[ -]+(?:and )?' + atom + r')*\b', amount_clause, re.I)
    meaningful = [n.rstrip('.') for n in amounts]
    if len(meaningful) != 1 or normalize_number(meaningful[0]) != normalize_number(token):
        fail('Ambiguous amount/role in quote.')
    return {'value': item.value, 'source': source, 'quote': quote,
            'chunk_id': resolved['chunk_id'] if resolved else None,
            'page': resolved['page'] if resolved else None}


def execute_calculation_plan(plan, question, matches, kind=None):
    if kind and plan.kind != kind:
        fail('Extraction kind differs from routed intent.')
    evidence = []
    if plan.kind == 'remaining_balance':
        if not plan.entitlement or not plan.used:
            fail('Extraction omitted entitlement or usage; evidence absence is not established.')
        if plan.categories:
            fail('Unexpected categories.')
        a, b = plan.entitlement, plan.used
        evidence += [verify(a, 'document', 'entitlement', question, matches),
                     verify(b, 'question', 'used', question, matches)]
        if (a.unit, a.period) != (b.unit, b.period) or a.unit != 'days':
            fail('Incompatible units or periods.', 'unsupported_calculation')
        result = Decimal(a.value) - Decimal(b.value)
        expression = f'{a.value} − {b.value} = {result} {a.unit}'
        assumption = 'Assumes no other usage in the stated period.'
    else:
        if not plan.categories:
            fail('Extraction omitted category costs or caps; evidence absence is not established.')
        if plan.entitlement or plan.used:
            fail('Unexpected balance inputs.')
        values, lines, seen, dimensions = [], [], set(), set()
        for category in plan.categories:
            name = category.category.casefold()
            if name in seen:
                fail('Duplicate reimbursement category.')
            seen.add(name)
            a, c = category.actual, category.cap
            evidence += [verify(a, 'question', 'actual', question, matches, name),
                         verify(c, 'document', 'cap', question, matches, name)]
            if (a.unit, a.period) != (c.unit, c.period) or a.unit == 'days':
                fail('Incompatible units or periods.', 'unsupported_calculation')
            if a.period in ('per_night', 'per_day') and not re.search(r'\b(?:one|1) approved (?:day|night)\b', question, re.I):
                fail('Only one explicitly approved day/night is supported.', 'unsupported_calculation')
            dimensions.add(a.unit)
            value = min(Decimal(a.value), Decimal(c.value))
            values.append(value)
            lines.append(f'{category.category}: min({a.value}, {c.value}) = {value} {a.unit}')
        if len(dimensions) != 1:
            fail('Currencies cannot be mixed.', 'unsupported_calculation')
        result = sum(values)
        expression = '\n'.join(lines)
        if len(values) > 1:
            expression += '\nTotal: ' + ' + '.join(map(str, values)) + f' = {result} {next(iter(dimensions))}'
        assumption = 'Applies to eligible costs under the quoted caps; approval and eligibility are not inferred.'
    citations = []
    for e in evidence:
        if e['source'] == 'document':
            line = f'“{e["quote"]}” [Page {e["page"]}, Chunk {e["chunk_id"]}]'
            if line not in citations:
                citations.append(line)
    return {'result': str(result), 'inputs': evidence,
            'answer': expression + '\n' + assumption + '\nSupporting evidence:\n' + '\n'.join(citations)}
