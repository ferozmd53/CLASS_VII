"""Create a fresh CLASS_VII workbook; never overwrite an existing path."""
from pathlib import Path
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
SHEETS = {
"Dashboard":["Metric","Value","Updated At"],
"Watchlist":["Exchange","Symbol","Token","Enabled","Timeframe"],
"Live":["Symbol","LTP","Open","High","Low","Close","RSI","StochRSI","SMA Stoch","BB Upper","BB Middle","BB Lower","Signal","Last Update"],"LiveIndicators":["Symbol","LTP","Open","High","Low","Close","RSI","StochRSI","SMA Stoch","BB Upper","BB Middle","BB Lower","Signal","Last Update"],
"Trade_Terminal":["Symbol"]+[""]*11+["Qty","Action","Entry Signal"]+[""]*4+["Exit Signal"]+[""]*3+["Target","Stop Loss","Trail","Latest Stop","Status","P&L"],
"OrderBook":["Order No","Exchange","Symbol","Side","Product","Qty","Filled Qty","Pending Qty","Order Type","Price","Trigger Price","Average Price","Status","Remarks","Order Time"],
"OpenPosition":["Symbol","Side","Qty","Entry Price","LTP","Stop Loss","Target","Latest Stop","P&L","Status"],
"BrokerPositions":["Exchange","Symbol","Net Qty","Buy Qty","Sell Qty","Average Price","LTP","P&L","Product"],
"Holdings":["Exchange","Symbol","Quantity","Average Price","LTP","P&L"],"Funds":["Limit","Value"],"Alerts":["Time","Level","Symbol","Message"],"User_Credential":["Field","Value"]}
def create_workbook(path):
    target=Path(path).expanduser().resolve()
    if target.exists(): raise FileExistsError(f"Refusing to overwrite existing workbook: {target}")
    target.parent.mkdir(parents=True,exist_ok=True)
    wb=Workbook(); first=wb.active
    for name,headers in SHEETS.items():
        ws=first if name=="Dashboard" else wb.create_sheet(name); ws.title=name
        if name == "Trade_Terminal":
            ws.append([""] * len(headers)); ws.append([""] * len(headers)); ws.append(headers)
            ws.freeze_panes="A4"
        else:
            ws.append(headers); ws.freeze_panes="A2"
        ws.auto_filter.ref=ws.dimensions
        for c in ws[1]:
            c.font=Font(bold=True,color="FFFFFF"); c.fill=PatternFill("solid",fgColor="263B5E")
            c.alignment=Alignment(horizontal="center")
        for i,h in enumerate(headers,1): ws.column_dimensions[get_column_letter(i)].width=max(12,min(24,len(str(h))+3))
    for row,label in enumerate(["User ID","Password","Login Method","TOTP Secret","Vendor/Client ID","API Secret","Unused","Unused","AUTH_CODE","Public IP"],2):
        wb["User_Credential"].cell(row,1,label)
    wb.save(target); wb.close(); return target
