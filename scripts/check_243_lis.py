#!/usr/bin/env python3
"""安装后的LIS只读验收：默认统计全量名单，可隐藏输入一个首页号检查新路径。"""
import argparse
from contextlib import closing
import getpass
import json

from run_243_lis_batch import runtime_profile


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--patient',action='store_true',help='院内隐藏输入首页SYXH，输出化验计数及归属原因')
    args=parser.parse_args()
    helper,cfg,hs=runtime_profile()
    with closing(hs.connect(cfg,timeout=15)) as cn:
        cn.timeout=120
        hs.validate_lis_source(cn)
        print('LIS_SOURCE='+hs.LIS_SOURCE_VERSION)
        if args.patient:
            with open('/dev/tty'):
                pid=getpass.getpass('首页SYXH（不回显）：')
            lab=hs.fetch_lis_for_home(cn,pid,cfg.hub_hospital_code)
            print('NEW_LIS_ASSIGNED_ROWS='+str(len(lab)))
            print(json.dumps(lab.attrs['lab_linkage'],ensure_ascii=False))
            try:
                bundle=hs.fetch_hospital_bundle(cn,pid,cfg.hub_hospital_code)
                print('AUDIT_SOURCE_PREFLIGHT=PASS；本次住院化验行数='+str(len(bundle['labs'])))
            except hs.HospitalLinkageError as exc:
                print('AUDIT_SOURCE_PREFLIGHT='+str(exc))
        else:
            homes=hs.list_lis_homes(cn,cfg.hub_hospital_code)
            print('CANDIDATE_HOMES='+str(len(homes)))
    print('只读检查结束；未调用LLM、未写审计结果、未修改中台。')


if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        from javert.data.hub_source import HospitalLinkageError
        code=str(exc) if isinstance(exc,(HospitalLinkageError,RuntimeError)) else type(exc).__name__
        raise SystemExit('STOP: '+code)
