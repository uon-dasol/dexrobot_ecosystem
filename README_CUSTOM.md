# DexHand 설치 및 사용 가이드

DexHand 하드웨어 제어(`pyzlg_dexhand`)와 촉각 센서 모니터링·관절 제어 UI(`apps/dexhand_monitor`)를 다룹니다.

## Notes

- 이 저장소는 **UI**와 **`pyzlg_dexhand` 서브모듈**에 대해서만 동작 확인을 진행했습니다. 그 외 서브모듈(`dexrobot_kinematics`, `dexrobot_mujoco`, `dexrobot_isaac`, `dexrobot_urdf`, `ros_compat`)은 호환성 확인과 검증을 하지 않았습니다.
- ecosystem 저장소는 원래 `pyzlg_dexhand` 서브모듈의 다른 버전(`316575a`, v0.1.0+16)을 가리키고 있었고, 현재는 `1ffd87c`(v0.1.3)로 변경했습니다.

## HW Specification

- 동작 전압 / 정지 전류: DC 24V / 0.2A
- 최대 전류: 7.4A
- 통신: CAN FD

## Installation

```bash
# 환경 설정
conda create -n dexhand python=3.11 pip -y
conda activate dexhand
python -m pip install -e pyzlg_dexhand
python -m pip install -e apps/dexhand_monitor    # UI
python -m pip install PyQt6

# USB 접근 권한 부여
id -nG
# canbus 그룹이 현재 사용자에 없다면
newgrp canbus
# canbus 그룹 자체가 없다면 (최초 1회): sudo pyzlg_dexhand/tools/setup_usb_can.sh 실행 후 재로그인
```

## Usage

### 관절 제어 (Demo)

```bash
# pyzlg_dexhand/config/config.yaml에서 사용할 손을 channel에 맞게 변경
python pyzlg_dexhand/tools/hardware_test/test_dexhand.py --hands right
python pyzlg_dexhand/tools/hardware_test/test_dexhand.py --hands left
```

### 관절 제어 (GUI)

```bash
python pyzlg_dexhand/examples/dexhand_gui.py
```

### 모니터링·관절 제어 (UI)

```bash
dexhand-monitor --hardware --hand right --channel 0
```

## Reference

> `dexrobot.feishu.cn` 문서는 Feishu 로그인이 필요합니다.

- [DexRobot Download Center](https://www.dex-robot.com/en/downloadCenter/detail)
- [ZLG USBCANFD-100U-mini Datasheet](https://www.zlg.cn/data/upload/software/Can/USBCANFD-100U_mini-EN.pdf)
- [DexRobot (GitHub)](https://github.com/DexRobot)
- [DexHand Product Introduction](https://dexrobot.feishu.cn/docx/Ni2ddbAtJoNCK5xJ08AcniHGnab)
- [DexHand SDK User Guide](https://dexrobot.feishu.cn/docx/GLSxdaXWFoLd9vxz7fPcxYD4nJb)
- [DexHand User Manual](https://dexrobot.feishu.cn/docx/JZeydVYPcoBW5Txyw06cXKaonsg)
