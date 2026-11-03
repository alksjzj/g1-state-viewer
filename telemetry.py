"""Additional G1 DDS schema missing in the installed Python SDK.

Layout: unitree_sdk2/include/unitree/idl/hg/SportModeState_.hpp.
No client API or publisher is used to obtain motion state.
"""

_MOTION_TYPE = None


def get_motion_type():
    global _MOTION_TYPE
    if _MOTION_TYPE is None:
        from dataclasses import dataclass
        from cyclonedds.idl import IdlStruct
        from cyclonedds.idl.annotations import final, autoid
        from cyclonedds.idl.types import uint32, float32

        @dataclass
        @final
        @autoid('sequential')
        class SportModeState(IdlStruct, typename='unitree_hg::msg::dds_::SportModeState_'):
            fsm_id: uint32 = 0
            fsm_mode: uint32 = 0
            task_id: uint32 = 0
            task_time: float32 = 0.0

        _MOTION_TYPE = SportModeState
    return _MOTION_TYPE
