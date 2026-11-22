"""Private test-finance reports, using the same filters as the workbench."""
import csv
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


STATUS = {'pending': '待审核', 'approved': '已通过', 'rejected': '已驳回', 'cancelled': '已撤销'}


def filter_rows(rows, filters, *, history=False):
    if not isinstance(filters, dict) or set(filters) - {'q','status','from','to','min','max','group','user','reviewer','timezone'}:
        raise ValueError('报表筛选条件无效')
    if any(not isinstance(value, str) or len(value) > 500 for value in filters.values()):
        raise ValueError('报表筛选条件无效')
    try:
        zone = ZoneInfo(filters.get('timezone') or 'UTC')
        start = date.fromisoformat(filters['from']) if filters.get('from') else None
        end = date.fromisoformat(filters['to']) if filters.get('to') else None
        minimum = Decimal(filters['min']) if filters.get('min') else None
        maximum = Decimal(filters['max']) if filters.get('max') else None
    except (ValueError, InvalidOperation, ZoneInfoNotFoundError):
        raise ValueError('报表日期、金额或时区无效') from None
    if start and end and start > end:
        raise ValueError('开始日期不能晚于结束日期。')
    if any(value is not None and (not value.is_finite() or value < 0 or value > 1000000) for value in (minimum, maximum)):
        raise ValueError('报表金额范围无效')
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValueError('最低金额不能高于最高金额')
    status = filters.get('status', 'all')
    if status not in {'all', *STATUS}:
        raise ValueError('报表状态无效')
    query = filters.get('q', '').strip().lower()
    result = []
    for row in rows:
        item = row['snapshot'] if history else row
        state = row['action'] if history else item['status']
        timestamp = row['created_at'] if history else item['created_at']
        day = datetime.fromisoformat(timestamp.replace('Z', '+00:00')).astimezone(zone).date()
        amount = Decimal(item['amount_cents']) / 100
        text = ' '.join(str(value or '') for value in [item['id'],item['project'],item['username'],item.get('user_note'),
            *item.get('groups', []),item.get('expense_group_name'),item.get('review_note'),
            row.get('note') if history else '',row.get('actor_name') if history else '']).lower()
        group = item.get('expense_group_id') or 'legacy:' + item.get('expense_group_name', '未分组')
        if ((query and query not in text) or (status != 'all' and state != status)
                or (start and day < start) or (end and day > end)
                or (minimum is not None and amount < minimum) or (maximum is not None and amount > maximum)
                or (filters.get('group') and filters['group'] != group)
                or (filters.get('user') and filters['user'] != item['username'])
                or (history and filters.get('reviewer') and filters['reviewer'] != row['actor_id'])):
            continue
        result.append(row)
    return result


def report_rows(items, report):
    if report == 'applications':
        headers = ['申请编号','提交时间','更新时间','账户','账户备注','当前分组','费用归属','报销项目','报销金额（元）','状态','资料版本','审核人','审核时间','审核意见']
        rows = [[item['id'],item['created_at'],item.get('updated_at',''),item['username'],item.get('user_note',''),
                 '、'.join(item.get('groups',[])),item.get('expense_group_name','未分组'),item['project'],
                 Decimal(item['amount_cents']) / 100,STATUS[item['status']],item.get('version',1),
                 item.get('reviewer_name') or '',item.get('reviewed_at') or '',item.get('review_note','')] for item in items]
        return '报销明细', headers, rows
    if report == 'history':
        headers = ['审批编号','申请编号','资料版本','审批时间','审核人','结果','审核意见','当前有效审批','被替代审批编号','账户','账户备注','费用归属','报销项目','报销金额（元）']
        rows = []
        for event in items:
            item = event['snapshot']
            rows.append([event['id'],event['application_id'],event['version'],event['created_at'],event['actor_name'],
                STATUS[event['action']],event['note'],'是' if event['is_current_review'] else '否',event.get('supersedes_event_id') or '',
                item['username'],item.get('user_note',''),item.get('expense_group_name','未分组'),item['project'],Decimal(item['amount_cents']) / 100])
        return '审批记录', headers, rows
    if report != 'groups':
        raise ValueError('报表类型无效')
    headers = ['费用分组编号','费用归属','有效申请笔数','申请总额（元）','待审核笔数','待审核金额（元）','已通过笔数','已通过金额（元）','已驳回笔数','已驳回金额（元）']
    groups = {}
    for item in items:
        if item['status'] == 'cancelled':
            continue
        key = item.get('expense_group_id') or 'legacy:' + item.get('expense_group_name','未分组')
        group = groups.setdefault(key, {'id':item.get('expense_group_id',''), 'name':item.get('expense_group_name','未分组'),
            'pending':[0,0], 'approved':[0,0], 'rejected':[0,0]})
        group[item['status']][0] += 1
        group[item['status']][1] += item['amount_cents']
    rows = []
    for group in groups.values():
        counts = [group[state] for state in ('pending','approved','rejected')]
        rows.append([group['id'],group['name'],sum(value[0] for value in counts),Decimal(sum(value[1] for value in counts))/100,
                     *[value for count,amount in counts for value in (count,Decimal(amount)/100)]])
    return '分组统计', headers, rows


def safe_text(value):
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(value))


def write_report(path: Path, format, title, headers, rows):
    if format == 'csv':
        with path.open('w', encoding='utf-8-sig', newline='') as output:
            writer = csv.writer(output)
            writer.writerow(headers)
            for row in rows:
                values = []
                for value in row:
                    if isinstance(value, str):
                        value = safe_text(value)
                        if value.lstrip().startswith(('=','+','-','@')) or value.startswith(('\t','\r','\n')):
                            value = "'" + value
                    values.append(value)
                writer.writerow(values)
        return
    if format != 'xlsx':
        raise ValueError('报表格式无效')
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet(title)
    sheet.freeze_panes = 'A2'
    heading = []
    for label in headers:
        cell = WriteOnlyCell(sheet, label)
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='5147DC')
        heading.append(cell)
    sheet.append(heading)
    for row in rows:
        values = []
        for value in row:
            cell = WriteOnlyCell(sheet, safe_text(value) if isinstance(value, str) else value)
            if isinstance(value, str):
                cell.data_type = 's'
            elif isinstance(value, Decimal):
                cell.number_format = '#,##0.00'
            values.append(cell)
        sheet.append(values)
    sheet.auto_filter.ref = f'A1:{get_column_letter(len(headers))}{len(rows)+1}'
    workbook.save(path)
