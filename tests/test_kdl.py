from datetime import date

from app.kdl import parse_order, parse_profile, parse_reference

PROFILE = """
<div class="profile-orders">
  <a href="javascript:void(0)" data-id="64473877">1053008033</a>
  <a href="javascript:void(0)" data-id="64015244">1052660626</a>
</div>
"""

ORDER = """
<div class="profile-order">
  <div class="profile-order__title">Заказ № 1053008033</div>
  <div class="profile-order__date">
    Дата создания: 06.10.2026 | Расчетная дата выполнения: 07.10.2026
  </div>
  <div class="profile-order__row _body">
    <div class="profile-order__cell _name">
      <input class="radiobox__input js-show-details" name="test" value="ALAT" type="radio">
      Аланинаминотрансфераза (АЛТ)
    </div>
    <div class="profile-order__cell _value">48.1 <span>Ед/л</span></div>
    <div class="profile-order__cell _normal">&lt;50.0</div>
    <div class="profile-order__cell _dimension">Ед/л</div>
  </div>
  <div class="profile-order__row _body">
    <div class="profile-order__cell _name">Гемоглобин</div>
    <div class="profile-order__cell _value">157 <span>г/л</span></div>
    <div class="profile-order__cell _normal">135 - 169</div>
    <div class="profile-order__cell _dimension">г/л</div>
  </div>
  <div class="profile-order__row _body">
    <div class="profile-order__cell _name">Цвет</div>
    <div class="profile-order__cell _value">соломенный</div>
    <div class="profile-order__cell _normal">желтый</div>
    <div class="profile-order__cell _dimension"></div>
  </div>
  <div class="profile-order__row _body">
    <div class="profile-order__cell _name">Лейкоциты</div>
    <div class="profile-order__cell _value">&lt;6.0 кл/мкл</div>
    <div class="profile-order__cell _normal">0.0 - 9.0</div>
    <div class="profile-order__cell _dimension">кл/мкл</div>
  </div>
</div>
"""


def test_parse_profile():
    orders = parse_profile(PROFILE)
    assert [o.number for o in orders] == ["1053008033", "1052660626"]
    assert orders[0].internal_id == "64473877"


def test_parse_order_skips_non_numeric():
    results = parse_order(ORDER)
    names = [r.name for r in results]
    assert names == ["Аланинаминотрансфераза (АЛТ)", "Гемоглобин"]
    assert all(r.order == "1053008033" for r in results)
    assert all(r.measured_at == date(2026, 10, 6) for r in results)


def test_parse_order_values():
    alat, hemoglobin = parse_order(ORDER)
    assert alat.value == 48.1
    assert alat.unit == "Ед/л"
    assert alat.code == "ALAT"
    assert alat.ref_low is None and alat.ref_high == 50.0
    assert hemoglobin.ref_low == 135.0 and hemoglobin.ref_high == 169.0
    assert hemoglobin.external_id == "1053008033:Гемоглобин"


def test_parse_reference():
    assert parse_reference("<50.0") == (None, 50.0)
    assert parse_reference("0.0 - 2.5") == (0.0, 2.5)
    assert parse_reference("135 - 169") == (135.0, 169.0)
    assert parse_reference("не обнаружено") == (None, None)
