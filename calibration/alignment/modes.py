"""Validated alignment-mode selection; no Truth fixtures or injection values."""
from calibration.alignment import sensors as multi

def is_four(mode):
    return mode in ('l1_y_rx_ry_rz','l1_y_rx_ry_rz_negative','l1_y_rx_ry_rz_contained','l1_y_rx_ry_rz_contained_negative')


def is_three(mode):
    return mode in ('l1_rx_ry_rz','l1_rx_ry_rz_negative') or is_four(mode)


def rotation_labels(mode):
    return [700104,700105,700106] if is_three(mode) else [700105,700106]


def is_coupled(mode):
    if multi.is_multi(mode):return True
    return mode in ('l1_ry_rz','l1_ry_rz_negative') or is_three(mode)


def axis(mode):
    if multi.is_multi(mode):return 'xyz'
    if is_three(mode):return 'xyz'
    if is_coupled(mode):return 'yz'
    return 'y' if mode in ('l1_ry','l1_ry_negative') else ('z' if mode in ('l1_rz','l1_rz_negative') else 'x')


def rotation_label(mode):
    return {'x':700104,'y':700105,'z':700106}[axis(mode)]


def labels(mode):
    if multi.is_multi(mode):return multi.labels(mode)
    if is_four(mode):return [700102,700104,700105,700106]
    if is_three(mode):return [700104,700105,700106]
    if is_coupled(mode):return [700105,700106]
    return [rotation_label(mode)] if mode!='l1_y_rx_l2_y' else [700102,700104,700202]


def config_name(mode):
    if multi.is_multi(mode):return multi.config_name(mode)
    if is_four(mode):return 'l1_global_y_rx_ry_rz'
    if is_three(mode):return 'l1_global_rx_ry_rz'
    if is_coupled(mode):return 'l1_global_ry_rz'
    return 'l1_y_rx_l2_y' if mode=='l1_y_rx_l2_y' else 'l1_global_r'+axis(mode)

