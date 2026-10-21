"""Formula evaluator (Master Plan §14): allow-list, Decimal, cfg() cycles,
limits, and a 1,000-expression fuzz that nothing escapes."""
import random
from decimal import Decimal

import pytest

from qa_core.formula import (
    MAX_NODES,
    FormulaError,
    evaluate,
    evaluate_setting,
    evaluate_value,
    validate_formula,
)


def ev(expr, **vars_):
    return evaluate_value(expr, vars_)


# --------------------------------------------------------------------- allowed
@pytest.mark.parametrize("expr, vars_, expected", [
    ("1 + 2 * 3", {}, Decimal(7)),
    ("(1 + 2) * 3", {}, Decimal(9)),
    ("7 // 2", {}, Decimal(3)),
    ("-7 // 2", {}, Decimal(-4)),
    ("7 % 3", {}, Decimal(1)),
    ("-7 % 3", {}, Decimal(2)),
    ("2 ** 4", {}, Decimal(16)),
    ("10 / 4", {}, Decimal("2.5")),
    ("min(3, 1, 2) + max(4, 9)", {}, Decimal(10)),
    ("round(2.345, 2)", {}, Decimal("2.35")),
    ("ceil(1.2) + floor(1.8) + abs(-3)", {}, Decimal(6)),
    ("clamp(15, 0, 10)", {}, Decimal(10)),
    ("x > 3 and not y", {"x": 5, "y": False}, True),
    ("1 < x <= 5", {"x": 5}, True),
    ("'a' if flag else 'b'", {"flag": True}, "a"),
    ("programmes * (2000000 if ownership == 'private' else 1000000)",
     {"programmes": 3, "ownership": "private"}, Decimal(6000000)),
    ("programmes * (2000000 if ownership == 'private' else 1000000)",
     {"programmes": 3, "ownership": "public"}, Decimal(3000000)),
])
def test_allowed(expr, vars_, expected):
    assert evaluate_value(expr, vars_) == expected


def test_arithmetic_is_decimal_not_float():
    result = ev("0.1 + 0.2")
    assert isinstance(result, Decimal)
    assert result == Decimal("0.3")
    assert ev("x * 3", x=Decimal("0.1")) == Decimal("0.3")


def test_returns_coercion():
    assert evaluate("2.6", returns="int")[0] == 3
    with pytest.raises(FormulaError):
        evaluate("1 + 1", returns="bool")
    with pytest.raises(FormulaError):
        evaluate("1 > 0", returns="decimal")


# --------------------------------------------------------------------- rejected
@pytest.mark.parametrize("expr", [
    "().__class__",
    "x.real",
    "[1, 2][0]",
    "{'a': 1}",
    "[1, 2]",
    "(1, 2)",
    "{1, 2}",
    "lambda: 1",
    "[i for i in (1, 2)]",
    "__import__('os')",
    "open('/etc/passwd')",
    "eval('1')",
    "exec('x=1')",
    "getattr(1, 'real')",
    "f'{1}'",
    "round(1, ndigits=2)",
    "max(*[1, 2])",
    "2 ** 5",
    "2 ** 0.5",
    "1 << 2",
    "1 & 2",
    "~1",
    "1 if 2",
    "x := 1",
    "1 is 1",
    "1 in (1,)",
    "unknown_name + 1",
    "cfg(name)",
    "cfg('a', 'b')",
    "clamp(1, 2)",
    "b'bytes'",
    "None",
    "1j",
    "",
    "1 +",
])
def test_rejected(expr):
    with pytest.raises(FormulaError):
        evaluate_value(expr, {"x": 1, "name": "k"})


def test_division_by_zero_is_formula_error():
    for expr in ("1 / 0", "1 // 0", "1 % 0", "0 ** -1"):
        with pytest.raises(FormulaError):
            ev(expr)


def test_node_limit():
    expr = " + ".join(["1"] * (MAX_NODES // 2 + 10))
    with pytest.raises(FormulaError):
        ev(expr)


def test_time_budget():
    with pytest.raises(FormulaError, match="too long"):
        evaluate("1 + 1", time_budget=-1)


def test_huge_numbers_do_not_escape():
    with pytest.raises(FormulaError):
        ev("((((10 ** 4) ** 4) ** 4) ** 4) ** 4 ** 4 ** 4 ** 4 ** 4 ** 4")


def test_undeclared_variable_rejected_at_save():
    with pytest.raises(FormulaError):
        validate_formula("a + b", ["a"])
    validate_formula("a + b", ["a", "b"])
    with pytest.raises(FormulaError):
        validate_formula("1", ["cfg"])


# --------------------------------------------------------------------- cfg()
def _getter(table):
    def get(key):
        if key not in table:
            raise FormulaError(f"no {key}")
        value = table[key]
        if isinstance(value, dict):
            return ("formula", value)
        return ("value", value)
    return get


def test_cfg_reads_values_and_formulas():
    table = {
        "base": 100,
        "per": {"expr": "cfg('base') * 2", "vars": []},
        "total": {"expr": "cfg('base') + n * cfg('per')", "vars": ["n"]},
    }
    snap = evaluate_setting("total", {"n": 3}, cfg_getter=_getter(table))
    assert Decimal(snap["result"]) == Decimal(700)
    assert snap["inputs"] == {"n": "3"}
    assert "base" in snap["cfg"] and "per" in snap["cfg"]


def test_cfg_cycle_detected():
    table = {
        "a": {"expr": "cfg('b') + 1", "vars": []},
        "b": {"expr": "cfg('c') + 1", "vars": []},
        "c": {"expr": "cfg('a') + 1", "vars": []},
    }
    with pytest.raises(FormulaError, match="cycle"):
        evaluate_setting("a", {}, cfg_getter=_getter(table))


def test_cfg_self_cycle_detected():
    table = {"a": {"expr": "cfg('a')", "vars": []}}
    with pytest.raises(FormulaError, match="cycle"):
        evaluate_setting("a", {}, cfg_getter=_getter(table))


def test_cfg_rejects_json_values():
    with pytest.raises(FormulaError):
        evaluate("cfg('j')", {}, cfg_getter=_getter({"j": [1, 2]}))


@pytest.mark.django_db
def test_seeded_fee_formula_against_real_settings(qa_seed):
    snap = evaluate_setting("qa.fee.programme_processing", {"programmes": 2, "ownership": "private"})
    assert Decimal(snap["result"]) == Decimal(4000000)
    snap = evaluate_setting("qa.fee.qa_request_processing", {"programmes": 5})
    assert Decimal(snap["result"]) == 0  # D15 unconfirmed: base and per-programme are 0


# --------------------------------------------------------------------- fuzz
ATOMS = ["1", "2.5", "0", "x", "y", "s", "True", "'t'", "cfg('k')", "-3", "1e3"]
EVIL = ["__import__('os')", "x.__class__", "().__class__.__bases__", "[1][0]", "lambda: 0",
        "open('f')", "eval('1')", "{}", "[]", "x[0]", "s.upper()", "1 << 64", "9 ** 9",
        "globals()", "[i for i in s]", "(yield)", "await x", "print(1)", "type(x)", "x.real"]
BINOPS = ["+", "-", "*", "/", "//", "%", "**", "==", "!=", "<", "<=", ">", ">=", "and", "or"]
FUNCS = ["min", "max", "round", "ceil", "floor", "abs", "clamp"]


def _random_expr(rng, depth=0):
    roll = rng.random()
    if depth > 4 or roll < 0.25:
        return rng.choice(EVIL) if rng.random() < 0.08 else rng.choice(ATOMS)
    if roll < 0.55:
        return f"({_random_expr(rng, depth + 1)} {rng.choice(BINOPS)} {_random_expr(rng, depth + 1)})"
    if roll < 0.65:
        return f"(not {_random_expr(rng, depth + 1)})"
    if roll < 0.75:
        return f"({_random_expr(rng, depth + 1)} if {_random_expr(rng, depth + 1)} else {_random_expr(rng, depth + 1)})"
    fn = rng.choice(FUNCS)
    n = {"clamp": 3, "ceil": 1, "floor": 1, "abs": 1, "round": rng.choice([1, 2])}.get(fn, rng.randint(1, 3))
    return f"{fn}({', '.join(_random_expr(rng, depth + 1) for _ in range(n))})"


def test_fuzz_1000_random_expressions_never_escape(monkeypatch):
    """Every outcome is a Decimal/bool/str result or a FormulaError. Nothing
    else may escape, and the builtins that would show an escape are never
    reached (they are patched to fail loudly)."""
    import builtins

    tripwire = []

    def trap(*a, **k):  # pragma: no cover - only runs if the sandbox leaks
        tripwire.append(a)
        raise AssertionError("sandbox escape")

    # __import__ and compile are left alone: pytest imports lazily and
    # ast.parse (the evaluator's parser) is built on compile().
    for name in ("eval", "exec", "open"):
        monkeypatch.setattr(builtins, name, trap)

    rng = random.Random(20261016)
    getter = _getter({"k": 7})
    outcomes = {"ok": 0, "rejected": 0}
    for _ in range(1000):
        expr = _random_expr(rng)
        try:
            result, _used = evaluate(expr, {"x": 3, "y": Decimal("0.5"), "s": "abc"}, cfg_getter=getter)
        except FormulaError:
            outcomes["rejected"] += 1
            continue
        assert isinstance(result, (Decimal, bool, str)), (expr, result)
        outcomes["ok"] += 1
    assert not tripwire
    assert outcomes["ok"] > 100 and outcomes["rejected"] > 50, outcomes
