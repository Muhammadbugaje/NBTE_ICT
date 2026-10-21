"""Safe formula evaluator (Master Plan §14).

Fees, capacity rules, ranking weights and transition conditions are
*formulas* stored as settings, so they change without a code release. This
module evaluates them without ever calling ``eval()``:

* The text is parsed with Python's standard :mod:`ast` (``mode="eval"``) and
  every node is checked against an **allow-list**:
    - numbers and strings (and ``True`` / ``False``);
    - names that are declared variables;
    - ``+ - * / // %`` and ``**`` with an exponent of at most 4;
    - comparisons (``== != < <= > >=``), ``and`` / ``or`` / ``not``;
    - ``x if c else y``;
    - calls to ``min max round ceil floor abs clamp`` and ``cfg('key')``.
* No attribute access, subscripts, lambdas, comprehensions, f-strings,
  keyword arguments, star-args or imports. Anything else is a FormulaError.
* Arithmetic is in :class:`decimal.Decimal`. Evaluation is limited to 1,000
  nodes and a 50 ms budget.
* ``cfg('key')`` reads another setting; a formula-typed setting is evaluated
  with the same variables, with cycle detection.
* :func:`evaluate_setting` returns a snapshot (expression, inputs, result) so
  an issued bill or assessment keeps "the parameters in effect at the time".
"""
from __future__ import annotations

import ast
import decimal
import math
import time
from dataclasses import dataclass, field
from decimal import Decimal

MAX_NODES = 1000
TIME_BUDGET_SECONDS = 0.050
MAX_EXPONENT = 4
MAX_EXPRESSION_LENGTH = 4000
MAX_STRING_LENGTH = 200
MAX_CFG_DEPTH = 10

ALLOWED_FUNCTIONS = ("min", "max", "round", "ceil", "floor", "abs", "clamp", "cfg")
RETURN_TYPES = ("decimal", "int", "bool", "str")

_BIN_OPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)
_CMP_OPS = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)
_UNARY_OPS = (ast.UAdd, ast.USub, ast.Not)
_BOOL_OPS = (ast.And, ast.Or)

# Decimal context: generous precision, every exceptional condition trapped.
_CONTEXT = decimal.Context(
    prec=34, rounding=decimal.ROUND_HALF_EVEN, Emax=999_999, Emin=-999_999,
    traps=[decimal.InvalidOperation, decimal.DivisionByZero, decimal.Overflow],
)


class FormulaError(ValueError):
    """The formula is not allowed, is malformed, or failed to evaluate."""


@dataclass
class _State:
    variables: dict
    cfg_getter: object
    deadline: float
    cfg_stack: list = field(default_factory=list)
    used_cfg: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Parsing / static validation
# ---------------------------------------------------------------------------
def parse(expr, variables=()):
    """Parse and validate ``expr``; return the AST. Raises FormulaError."""
    if not isinstance(expr, str):
        raise FormulaError("A formula must be text.")
    text = expr.strip()
    if not text:
        raise FormulaError("The formula is empty.")
    if len(text) > MAX_EXPRESSION_LENGTH:
        raise FormulaError(f"The formula is longer than {MAX_EXPRESSION_LENGTH} characters.")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise FormulaError(f"Syntax error: {exc.msg}.") from None
    except (ValueError, RecursionError, MemoryError):
        raise FormulaError("The formula could not be parsed.") from None

    allowed_names = set(variables)
    count = 0
    for node in ast.walk(tree):
        count += 1
        if count > MAX_NODES:
            raise FormulaError(f"The formula has more than {MAX_NODES} parts.")
        _check_node(node, allowed_names)
    return tree


def _check_node(node, allowed_names):
    if isinstance(node, (ast.Expression, ast.Load)):
        return
    if isinstance(node, ast.Constant):
        value = node.value
        if isinstance(value, bool) or isinstance(value, (int, float)):
            if isinstance(value, float) and not math.isfinite(value):
                raise FormulaError("Numbers must be finite.")
            return
        if isinstance(value, str):
            if len(value) > MAX_STRING_LENGTH:
                raise FormulaError("Text values are limited to 200 characters.")
            return
        raise FormulaError(f"Values of type {type(value).__name__} are not allowed.")
    if isinstance(node, ast.Name):
        if node.id in ("True", "False"):
            return
        if node.id in allowed_names:
            return
        if node.id in ALLOWED_FUNCTIONS:
            return  # validated at the Call node; a bare function name fails there
        raise FormulaError(f"Unknown name '{node.id}'. Declared variables: "
                           f"{', '.join(sorted(allowed_names)) or 'none'}.")
    if isinstance(node, ast.BinOp):
        if not isinstance(node.op, _BIN_OPS):
            raise FormulaError(f"Operator {type(node.op).__name__} is not allowed.")
        return
    if isinstance(node, _BIN_OPS + _CMP_OPS + _UNARY_OPS + _BOOL_OPS):
        return
    if isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, _UNARY_OPS):
            raise FormulaError(f"Operator {type(node.op).__name__} is not allowed.")
        return
    if isinstance(node, ast.BoolOp):
        if not isinstance(node.op, _BOOL_OPS):
            raise FormulaError("Only 'and' and 'or' are allowed.")
        return
    if isinstance(node, ast.Compare):
        for op in node.ops:
            if not isinstance(op, _CMP_OPS):
                raise FormulaError(f"Comparison {type(op).__name__} is not allowed.")
        return
    if isinstance(node, ast.IfExp):
        return
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in ALLOWED_FUNCTIONS:
            raise FormulaError("Only these functions may be called: " + ", ".join(ALLOWED_FUNCTIONS) + ".")
        if node.keywords:
            raise FormulaError("Keyword arguments are not allowed.")
        if any(isinstance(a, ast.Starred) for a in node.args):
            raise FormulaError("Star-arguments are not allowed.")
        name = node.func.id
        if name == "cfg":
            if len(node.args) != 1 or not (isinstance(node.args[0], ast.Constant)
                                           and isinstance(node.args[0].value, str)):
                raise FormulaError("cfg() takes exactly one setting key in quotes.")
        elif name == "clamp" and len(node.args) != 3:
            raise FormulaError("clamp(value, low, high) takes three arguments.")
        elif name in ("ceil", "floor", "abs") and len(node.args) != 1:
            raise FormulaError(f"{name}() takes one argument.")
        elif name == "round" and len(node.args) not in (1, 2):
            raise FormulaError("round() takes one or two arguments.")
        elif name in ("min", "max") and len(node.args) < 1:
            raise FormulaError(f"{name}() needs at least one argument.")
        return
    raise FormulaError(f"{type(node).__name__} is not allowed in a formula.")


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
def _to_value(value):
    """Normalise an input to Decimal / bool / str."""
    if isinstance(value, bool):
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise FormulaError("Inputs must be finite numbers.")
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise FormulaError("Inputs must be finite numbers.")
        return Decimal(repr(value))
    if isinstance(value, str):
        if len(value) > MAX_STRING_LENGTH:
            raise FormulaError("Text inputs are limited to 200 characters.")
        return value
    if value is None:
        raise FormulaError("An input has no value.")
    raise FormulaError(f"Inputs of type {type(value).__name__} are not supported.")


def _num(value, what="value"):
    if isinstance(value, bool) or not isinstance(value, Decimal):
        raise FormulaError(f"Expected a number for {what}.")
    return value


def _eval(node, st):
    if time.perf_counter() > st.deadline:
        raise FormulaError("The formula took too long to evaluate.")

    if isinstance(node, ast.Expression):
        return _eval(node.body, st)
    if isinstance(node, ast.Constant):
        return _to_value(node.value)
    if isinstance(node, ast.Name):
        if node.id == "True":
            return True
        if node.id == "False":
            return False
        if node.id in ALLOWED_FUNCTIONS:
            raise FormulaError(f"{node.id} must be called, e.g. {node.id}(...).")
        if node.id not in st.variables:
            raise FormulaError(f"No value was given for '{node.id}'.")
        return st.variables[node.id]
    if isinstance(node, ast.UnaryOp):
        operand = _eval(node.operand, st)
        if isinstance(node.op, ast.Not):
            return not _truthy(operand)
        number = _num(operand, "a sign")
        return _CONTEXT.plus(number) if isinstance(node.op, ast.UAdd) else _CONTEXT.minus(number)
    if isinstance(node, ast.BinOp):
        left = _eval(node.left, st)
        right = _eval(node.right, st)
        return _binop(node.op, left, right)
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            result = True
            for value_node in node.values:
                result = _eval(value_node, st)
                if not _truthy(result):
                    return result
            return result
        result = False
        for value_node in node.values:
            result = _eval(value_node, st)
            if _truthy(result):
                return result
        return result
    if isinstance(node, ast.Compare):
        left = _eval(node.left, st)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval(comparator, st)
            if not _compare(op, left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.IfExp):
        return _eval(node.body, st) if _truthy(_eval(node.test, st)) else _eval(node.orelse, st)
    if isinstance(node, ast.Call):
        return _call(node, st)
    raise FormulaError(f"{type(node).__name__} is not allowed in a formula.")


def _truthy(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, Decimal):
        return value != 0
    if isinstance(value, str):
        return value != ""
    raise FormulaError("Unsupported value.")


def _binop(op, left, right):
    if isinstance(op, ast.Add) and isinstance(left, str) and isinstance(right, str):
        if len(left) + len(right) > MAX_STRING_LENGTH:
            raise FormulaError("Text values are limited to 200 characters.")
        return left + right
    a, b = _num(left, "arithmetic"), _num(right, "arithmetic")
    try:
        if isinstance(op, ast.Add):
            return _CONTEXT.add(a, b)
        if isinstance(op, ast.Sub):
            return _CONTEXT.subtract(a, b)
        if isinstance(op, ast.Mult):
            return _CONTEXT.multiply(a, b)
        if isinstance(op, ast.Div):
            if b == 0:
                raise FormulaError("Division by zero.")
            return _CONTEXT.divide(a, b)
        if isinstance(op, ast.FloorDiv):
            if b == 0:
                raise FormulaError("Division by zero.")
            return (_CONTEXT.divide(a, b)).to_integral_value(rounding=decimal.ROUND_FLOOR, context=_CONTEXT)
        if isinstance(op, ast.Mod):
            if b == 0:
                raise FormulaError("Division by zero.")
            # Python semantics (result takes the sign of the divisor).
            return _CONTEXT.subtract(a, _CONTEXT.multiply(
                b, (_CONTEXT.divide(a, b)).to_integral_value(rounding=decimal.ROUND_FLOOR, context=_CONTEXT)))
        if isinstance(op, ast.Pow):
            if b != b.to_integral_value() or abs(b) > MAX_EXPONENT:
                raise FormulaError(f"Powers are limited to whole exponents of at most {MAX_EXPONENT}.")
            if a == 0 and b < 0:
                raise FormulaError("Division by zero.")
            return _CONTEXT.power(a, b)
    except FormulaError:
        raise
    except (ArithmeticError, ValueError) as exc:
        raise FormulaError(f"Arithmetic error: {type(exc).__name__}.") from None
    raise FormulaError("Operator not allowed.")


def _compare(op, left, right):
    both_numbers = (isinstance(left, Decimal) and not isinstance(left, bool)
                    and isinstance(right, Decimal) and not isinstance(right, bool))
    if isinstance(op, ast.Eq):
        return left == right if (both_numbers or type(left) is type(right)) else False
    if isinstance(op, ast.NotEq):
        return left != right if (both_numbers or type(left) is type(right)) else True
    if not (both_numbers or (isinstance(left, str) and isinstance(right, str))):
        raise FormulaError("Only numbers (or two texts) can be ordered.")
    if isinstance(op, ast.Lt):
        return left < right
    if isinstance(op, ast.LtE):
        return left <= right
    if isinstance(op, ast.Gt):
        return left > right
    if isinstance(op, ast.GtE):
        return left >= right
    raise FormulaError("Comparison not allowed.")


def _call(node, st):
    name = node.func.id
    if name == "cfg":
        return _cfg(node.args[0].value, st)
    args = [_eval(a, st) for a in node.args]
    try:
        if name in ("min", "max"):
            numbers = [_num(a, name + "()") for a in args]
            return min(numbers) if name == "min" else max(numbers)
        if name == "abs":
            return _CONTEXT.abs(_num(args[0], "abs()"))
        if name == "ceil":
            return _num(args[0], "ceil()").to_integral_value(rounding=decimal.ROUND_CEILING, context=_CONTEXT)
        if name == "floor":
            return _num(args[0], "floor()").to_integral_value(rounding=decimal.ROUND_FLOOR, context=_CONTEXT)
        if name == "round":
            value = _num(args[0], "round()")
            places = Decimal(0) if len(args) == 1 else _num(args[1], "round() places")
            if places != places.to_integral_value() or not (0 <= places <= 10):
                raise FormulaError("round() places must be a whole number from 0 to 10.")
            quantum = Decimal(1).scaleb(-int(places))
            return value.quantize(quantum, rounding=decimal.ROUND_HALF_UP, context=_CONTEXT)
        if name == "clamp":
            value, low, high = (_num(a, "clamp()") for a in args)
            if low > high:
                raise FormulaError("clamp() needs low <= high.")
            return max(low, min(high, value))
    except FormulaError:
        raise
    except (ArithmeticError, ValueError):
        raise FormulaError(f"{name}() failed.") from None
    raise FormulaError(f"Function {name}() is not allowed.")


def _default_cfg_getter(key):
    import config

    from config.models import ConfigEntry

    entry = ConfigEntry.objects.filter(key=key, scope="global").first()
    if entry is None:
        raise FormulaError(f"cfg('{key}'): no such setting.")
    if entry.value_type == "formula":
        return ("formula", entry.parsed())
    return ("value", config.get(key))


def _cfg(key, st):
    if key in st.cfg_stack:
        chain = " → ".join(st.cfg_stack + [key])
        raise FormulaError(f"cfg() cycle: {chain}.")
    if len(st.cfg_stack) >= MAX_CFG_DEPTH:
        raise FormulaError("cfg() is nested too deeply.")
    kind, payload = st.cfg_getter(key)
    if kind == "formula":
        st.cfg_stack.append(key)
        try:
            tree = parse(payload["expr"], payload.get("vars", ()))
            value = _eval(tree, st)
        finally:
            st.cfg_stack.pop()
    else:
        value = payload
        if isinstance(value, (list, dict)) or value is None:
            raise FormulaError(f"cfg('{key}') is not a number, yes/no or text.")
        value = _to_value(value)
    st.used_cfg[key] = _snapshot_value(value)
    return value


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def evaluate(expr, variables=None, *, declared=None, returns=None, cfg_getter=None,
             time_budget=None, _cfg_stack=None):
    """Evaluate ``expr`` with ``variables``. Returns Decimal, bool or str.

    ``declared`` lists the names the formula may use (default: the keys of
    ``variables``). ``returns`` (decimal/int/bool/str) coerces/validates the
    result. Raises FormulaError for anything not allowed.
    """
    variables = dict(variables or {})
    declared = list(declared) if declared is not None else list(variables)
    tree = parse(expr, declared)
    values = {name: _to_value(variables[name]) for name in declared if name in variables}
    budget = TIME_BUDGET_SECONDS if time_budget is None else time_budget
    st = _State(variables=values, cfg_getter=cfg_getter or _default_cfg_getter,
                deadline=time.perf_counter() + budget,
                cfg_stack=list(_cfg_stack or []))
    try:
        result = _eval(tree, st)
    except RecursionError:
        raise FormulaError("The formula is nested too deeply.") from None
    except (TypeError, ArithmeticError, ValueError) as exc:
        # Defence in depth: nothing but FormulaError may leave the evaluator.
        if isinstance(exc, FormulaError):
            raise
        raise FormulaError(f"The formula could not be evaluated ({type(exc).__name__}).") from None
    return _coerce(result, returns), st.used_cfg


def _coerce(result, returns):
    if returns in (None, "", "any"):
        return result
    if returns not in RETURN_TYPES:
        raise FormulaError(f"Unknown result type '{returns}'.")
    if returns == "bool":
        if not isinstance(result, bool):
            raise FormulaError("This formula must give yes/no (a comparison).")
        return result
    if returns == "str":
        if not isinstance(result, str):
            raise FormulaError("This formula must give text.")
        return result
    if isinstance(result, bool) or not isinstance(result, Decimal):
        raise FormulaError("This formula must give a number.")
    if returns == "int":
        return int(result.to_integral_value(rounding=decimal.ROUND_HALF_UP))
    return result


def _snapshot_value(value):
    if isinstance(value, Decimal):
        return str(value)
    return value


def evaluate_value(expr, variables=None, **kwargs):
    """Like :func:`evaluate` but returns only the result."""
    return evaluate(expr, variables, **kwargs)[0]


def evaluate_setting(key, variables=None, *, cfg_getter=None):
    """Evaluate the formula-typed setting ``key``; return a snapshot dict:

        {"key", "expr", "vars", "inputs", "cfg", "result", "returns", "evaluated_at"}
    """
    from django.utils import timezone

    getter = cfg_getter or _default_cfg_getter
    kind, payload = getter(key)
    if kind != "formula":
        raise FormulaError(f"{key} is not a formula setting.")
    variables = dict(variables or {})
    missing = [v for v in payload.get("vars", []) if v not in variables]
    if missing:
        raise FormulaError("Missing inputs: " + ", ".join(missing) + ".")
    result, used = evaluate(payload["expr"], variables, declared=payload.get("vars", []),
                            returns=payload.get("returns"), cfg_getter=getter, _cfg_stack=[key])
    return {
        "key": key,
        "expr": payload["expr"],
        "vars": list(payload.get("vars", [])),
        "inputs": {k: _snapshot_value(_to_value(v)) for k, v in variables.items()},
        "cfg": used,
        "result": _snapshot_value(result),
        "returns": payload.get("returns", "decimal"),
        "evaluated_at": timezone.now().isoformat(),
    }


def validate_formula(expr, declared=(), returns=None):
    """Static validation used when a formula is saved: parses, checks the
    allow-list and checks every name is declared. Returns None or raises."""
    parse(expr, declared)
    if returns not in (None, "") and returns not in RETURN_TYPES:
        raise FormulaError(f"Unknown result type '{returns}'. Use one of: {', '.join(RETURN_TYPES)}.")
    for name in declared:
        if not isinstance(name, str) or not name.isidentifier() or name in ALLOWED_FUNCTIONS \
                or name.startswith("_") or name in ("True", "False"):
            raise FormulaError(f"'{name}' cannot be used as a variable name.")


def validate_formula_setting(entry):
    """`config` value validator for formula-typed ConfigEntry rows."""
    from django.core.exceptions import ValidationError

    try:
        data = entry.parsed()
        validate_formula(data["expr"], data.get("vars", []), data.get("returns"))
    except FormulaError as exc:
        raise ValidationError({"value": f"Formula rejected: {exc}"})
