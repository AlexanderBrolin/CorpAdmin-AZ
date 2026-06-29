import openpyxl
import importlib.util
import pathlib

spec = importlib.util.spec_from_file_location(
    "mig", pathlib.Path(__file__).parent / "migrate_google_to_local.py")
mig = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mig)


def _wb(tmp_path, rows):
    """rows: list of (email, password, fired) -> xlsx with header on the right sheet."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = mig.SHEET
    ws.append(["ФИО", "Должность", "Аккаунт GSUite", "Пароль GSUite",
               "Пароль itscrm", "Уволен", "Объект", "Рюкзак"])
    for email, pw, fired in rows:
        ws.append(["fio", "pos", email, pw, "", fired, "obj", "False"])
    p = tmp_path / "reg.xlsx"
    wb.save(p)
    return str(p)


def test_parse_maps_columns_and_lowercases_email(tmp_path):
    reg = mig.parse_registry(_wb(tmp_path, [("User@Polati.ru", "Pass1234", "")]))
    assert reg == {"user@polati.ru": [("Pass1234", False)]}


def test_parse_skips_rows_without_email(tmp_path):
    reg = mig.parse_registry(_wb(tmp_path, [(None, "x", ""), ("a@polati.ru", "y", "")]))
    assert list(reg) == ["a@polati.ru"]


def test_resolve_single():
    assert mig.resolve([("Pass1234", False)]) == ("Pass1234", False, "ok")


def test_resolve_duplicate_same_password_ok():
    assert mig.resolve([("Same1", False), ("Same1", False)]) == ("Same1", False, "ok")


def test_resolve_conflict():
    pw, fired, st = mig.resolve([("A1", False), ("B2", False)])
    assert pw is None and st == "conflict"


def test_resolve_prefers_active_over_fired():
    assert mig.resolve([("Old1", True), ("New2", False)]) == ("New2", False, "ok")


def test_resolve_all_fired_marks_fired():
    pw, fired, st = mig.resolve([("X1", True)])
    assert fired is True


def test_resolve_empty():
    assert mig.resolve([("", False)]) == (None, False, "empty")


def test_classify_settable(tmp_path):
    reg = mig.parse_registry(_wb(tmp_path, [("a@polati.ru", "Pass1234", "")]))
    cats, pwmap = mig.classify({"a@polati.ru"}, reg)
    assert cats["settable"] == ["a@polati.ru"] and pwmap["a@polati.ru"] == "Pass1234"


def test_classify_fired_skipped(tmp_path):
    reg = mig.parse_registry(_wb(tmp_path, [("a@polati.ru", "Pass1234", "Уволен")]))
    cats, pwmap = mig.classify({"a@polati.ru"}, reg)
    assert cats["fired"] == ["a@polati.ru"] and "a@polati.ru" not in pwmap


def test_classify_empty_and_norow(tmp_path):
    reg = mig.parse_registry(_wb(tmp_path, [("a@polati.ru", "", "")]))
    cats, _ = mig.classify({"a@polati.ru", "b@polati.ru"}, reg)
    assert cats["empty"] == ["a@polati.ru"] and cats["norow"] == ["b@polati.ru"]


def test_classify_conflict(tmp_path):
    reg = mig.parse_registry(_wb(tmp_path, [("a@polati.ru", "A1", ""), ("a@polati.ru", "B2", "")]))
    cats, _ = mig.classify({"a@polati.ru"}, reg)
    assert cats["conflict"] == ["a@polati.ru"]


def test_classify_toolong_skipped(tmp_path):
    long = "x" * 73
    reg = mig.parse_registry(_wb(tmp_path, [("a@polati.ru", long, "")]))
    cats, pwmap = mig.classify({"a@polati.ru"}, reg)
    assert cats["toolong"] == ["a@polati.ru"] and "a@polati.ru" not in pwmap


def test_make_hash_verifies():
    h = mig.make_hash("Pass1234")
    assert h.startswith("$2b$") and mig.pwd_context.verify("Pass1234", h)
