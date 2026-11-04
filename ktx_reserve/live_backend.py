"""환경 신뢰 설정과 기기 ID를 고정한 실제 pykorail 클라이언트 생성."""
import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path

from pykorail import DeviceProfile, Korail
from pykorail.device import random_profile


def device_state(directory: Path):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / "device.json"
    if path.exists():
        data = json.loads(path.read_text())
        # Upgrade the old profile-only file without changing its device profile.
        profile = DeviceProfile(**data.get("profile", data))
        if "profile" in data:
            if data.get("android_id") != profile.android_id:
                raise ValueError("저장된 Android ID와 기기 프로파일이 다릅니다")
            return profile, profile.android_id
    else:
        profile = random_profile()
    android_id = profile.android_id
    descriptor, temporary = tempfile.mkstemp(prefix="device-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump({"profile": asdict(profile), "android_id": android_id}, stream)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return profile, android_id


def create_client(directory: Path):
    profile, android_id = device_state(directory)
    client = Korail(device_profile=profile, android_id=android_id)
    try:
        # The pinned SDK exposes no session constructor option. This integration
        # point is covered by tests against its real installed session classes.
        session = client._api._session._session
        session.timeout = 30
        for name in ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "SSL_CERT_FILE"):
            bundle = os.environ.get(name)
            if bundle:
                if not Path(bundle).is_file():
                    raise ValueError(f"{name}의 CA 파일을 찾을 수 없습니다")
                session.verify = bundle
                break
        # Preserve inherited proxy routing; never disable certificate verification.
        return client
    except BaseException:
        client.close()
        raise
