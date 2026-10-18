"""Human-readable export of saved business credentials; no network or mutation."""
from datetime import datetime


def export_text(config, ids=None):
    nodes = (config or {}).get('nodes', [])
    available = {n['id']: n for n in nodes}
    if ids is not None:
        if not isinstance(ids, list) or not ids or any(not isinstance(x, str) or x not in available for x in ids):
            raise ValueError('请选择有效的已保存节点，空选不会导出全部')
        chosen = set(ids)
        nodes = [n for n in nodes if n['id'] in chosen]
    if not nodes:
        raise ValueError('没有可导出的已保存 Web 或评测机节点')
    def value(v):
        return str(v) if v is not None and v != '' else '【缺失：本机未保存】'
    lines = ['CSGOJ 部署账号与口令', '导出时间：' + datetime.now().astimezone().isoformat(timespec='seconds'),
             '来源：当前 Windows 工作台已保存的加密配置。未连接服务器、未修改或生成口令。',
             '管理员口令为部署或接管时保存值；若曾在 OJ 修改，本机记录可能已过期。',
             '本文含明文业务口令，不含 SSH/sudo 密码或私钥。只导出所选节点，不附带未选中 Web 的口令。',
             '节点数量：' + str(len(nodes)), '']
    for n in nodes:
        lines.extend(['=' * 60, '节点：' + n['id'] + '（' + ('Web' if n['kind'] == 'web' else '评测机') + '）',
                      '服务器：' + value(n.get('host')), '项目目录：' + value(n.get('path')),
                      '角色：' + ('备用' if n.get('role') == 'standby' else '启用'),
                      '部署记录：' + ('已部署/已接管（当前在线状态未检测）' if n.get('deployed') else '未记录已部署；以下可能为待部署口令')])
        if n['kind'] == 'web':
            s = n.get('secrets') or {}
            lines.extend(['OJ 地址：http://' + str(n.get('host', '')) + ':' + str(n.get('port', 20080)) + '/cpcsys/contest',
                          '管理员账号：' + value(n.get('adminUser')), '管理员密码：' + value(s.get('admin')),
                          'MySQL root 账号：root', 'MySQL root 口令：' + value(s.get('sqlRoot')),
                          '数据库业务口令：' + value(s.get('sqlUser')),
                          'phpMyAdmin 页面口令：' + value(s.get('myadmin'))])
        else:
            lines.extend(['绑定 Web：' + value(n.get('web')), '评测账号：' + value(n.get('judgeUser')),
                          '评测密码：' + value(n.get('judgePassword')), 'pods：' + value(n.get('pods'))])
        lines.append('')
    # BOM makes double-click opening in Windows Notepad reliably preserve Chinese.
    return ('\ufeff' + '\r\n'.join(lines)).encode('utf-8')
