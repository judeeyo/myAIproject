"""오프라인 실행용 모의 backend. 코레일 프로토콜이나 실제 조회 결과가 아니다."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from types import SimpleNamespace


class PykorailError(Exception):
    pass


class KorailError(PykorailError):
    def __init__(self, message="demo error", code=""):
        super().__init__(message)
        self.code = code


class AccessRestrictedError(KorailError): pass
class LoginFailedError(KorailError): pass
class NeedToLoginError(KorailError): pass
class NoResultsError(KorailError): pass
class PastDepartureError(KorailError): pass
class SoldOutError(KorailError): pass
class StationNotFoundError(KorailError): pass


class TrainType(Enum):
    KTX = "KTX"
    ALL = "전체"
    SAEMAEUL = "새마을"
    MUGUNGHWA = "무궁화"
    ITX_CHEONGCHUN = "ITX-청춘"


class ReserveOption(Enum):
    GENERAL_ONLY = 1
    GENERAL_FIRST = 2
    SPECIAL_ONLY = 3
    SPECIAL_FIRST = 4


@dataclass
class AdultPassenger:
    count: int


class ChildPassenger(AdultPassenger): pass
class SeniorPassenger(AdultPassenger): pass


@dataclass
class Card:
    number: str
    password: str
    verify_number: str
    expire: str


@dataclass
class DeviceProfile:
    model: str = "OFFLINE-DEMO"


def random_profile():
    return DeviceProfile()


@dataclass
class Train:
    departure: datetime
    train_no: str
    dep_name: str
    arr_name: str
    train_type_name: str = "모의 KTX"

    @property
    def dep_time(self):
        return self.departure.strftime("%H%M%S")

    def has_general_seat(self): return True
    def has_special_seat(self): return True
    def has_seat(self): return True


class Korail:
    """선택한 조건에 맞춘 가상 열차 한 편. 네트워크·파일 접근 없음."""
    name = "데모 사용자"
    membership_number = "DEMO"

    def __init__(self, config):
        self.config = config
        self.stations = SimpleNamespace(ensure_exist=self.ensure_exist)
        self.trains = SimpleNamespace(search=self.search)
        self.reservations = SimpleNamespace(create=self.create, cancel=self.cancel, pay=self.pay)

    def __enter__(self): return self
    def __exit__(self, *args): return False
    def login(self, *args): pass

    def ensure_exist(self, dep, arr):
        if not dep.strip() or not arr.strip():
            raise StationNotFoundError("역 이름이 비어 있습니다")

    def search(self, dep, arr, *, depart_after, train_type, **kwargs):
        number = sorted(self.config.trains)[0] if self.config.trains else "105"
        return [Train(depart_after, number, dep, arr, "모의 " + train_type.value)]

    def create(self, train, passengers, option):
        count = sum(p.count for p in passengers)
        return SimpleNamespace(
            is_waiting=False,
            seats=[SimpleNamespace(car_no="1", seat_no=str(15 + i)) for i in range(count)],
            buy_limit=datetime.now(timezone.utc) + timedelta(minutes=10),
            price=0, pnr_no="DEMO-NOT-A-TICKET",
        )

    def cancel(self, reservation): pass

    def pay(self, *args):
        raise PykorailError("데모에서는 결제를 지원하지 않습니다")
