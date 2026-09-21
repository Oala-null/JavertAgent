"""执行真实前端函数的离线检查；浏览器视觉检查另有记录。"""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_lis_rendering_escaping_and_cache_expiry(tmp_path):
    node=shutil.which('node')
    if not node:
        pytest.skip('Node不可用')
    source=(Path(__file__).parents[1]/'src/javert/web/static/app.js').read_text()
    escape=source[source.index('  function _esc('):source.index('  // ---------- 原始数据 fetch')]
    fetch=source[source.index('  var _rawCache ='):source.index('  function _notesPanelHtml')]
    render=source[source.index('  function _labTableHtml'):source.index('  // ---------- 原始病历 modal')]
    code='''const assert=require('node:assert/strict');
let now=0, calls=0;
Date.now=()=>now;
const fetch=async()=>{ calls++; return {ok:true,json:async()=>({n_labs:calls})}; };
'''+escape+fetch+render+'''
(async()=>{
 const data={labs:[{item:'<img src=x onerror=alert(1)>',result:'1',date:'2026/09/01'}],
 pending_labs:[{item:'PENDING',result:'2',linkage_note:'其他次住院'}],exams:[],
 lab_linkage:{source:'medical-record-v1',assigned_reports:1,pending_reports:1}};
 const html=_labsPanelHtml(data,false);
 assert(html.includes('本次住院 · 检验化验 (1)'));
 assert(html.includes('未用于本次审计'));
 assert(html.includes('其他次住院'));
 assert(!html.includes('<img'));
 assert(html.includes('&lt;img'));
 assert(!_labTableHtml(data.pending_labs,true).includes('data-name='));
 await fetchRaw('CASE_A'); await fetchRaw('CASE_A'); assert.equal(calls,1);
 now=31001; await fetchRaw('CASE_A'); assert.equal(calls,2);
 await fetchRaw('__proto__'); await fetchRaw('__proto__'); assert.equal(calls,3);
 console.log('FRONTEND_LIS_CHECK=PASS');
})().catch(e=>{console.error(e);process.exit(1)});
'''
    path=tmp_path/'check.js'
    path.write_text(code)
    result=subprocess.run([node,str(path)],capture_output=True,text=True,check=True)
    assert 'FRONTEND_LIS_CHECK=PASS' in result.stdout
