"""Evaluator-only business definitions; never included in proposal prompts.

Known identities are manually registered from the sources below. Unknown
concepts are quarantined, not endorsed by an LLM or dimensional analysis.
"""
import sympy as sp

SOURCES={
    'STR':{'url':'https://www.costar.com/products/str-benchmark/resources/glossary',
           'checked':'2026-11-20','basis':'Occupancy, ADR, RevPAR; period supply equals rooms times days.'},
    'IRS':{'url':'https://www.irs.gov/publications/p334',
           'checked':'2026-11-20','basis':'Chapter 7: net receipts subtract returns/allowances; gross profit subtracts COGS; gross-profit percentage divides by net receipts.'},
    'LOCAL':{'url':None,'checked':'2026-11-20',
             'basis':'Explicit limited benchmark contract, not an external standard: operating result excludes other income, finance and tax; unit selling price uses net sales/net sold units; historical growth uses comparable net sales.'},
}

# Independent semantic basis. A proposed metric ID may be completely different.
P,D,A,S,R=sp.symbols('physical_rooms period_days available_room_nights sold_room_nights room_revenue',positive=True)
G,T,C,O,U,N0=sp.symbols('gross_receipts returns cogs opex net_units prior_net_sales',positive=True)
SYMBOLS=[P,D,A,S,R,G,T,C,O,U,N0]
NET=G-T


def spec(expr,unit,source,zh,en):
    return {'expression':expr,'unit':unit,'source':source,'zh':zh,'en':en}


ROOM_NIGHT={'room':1,'day':1}
PRICE={'CNY':1,'room':-1,'day':-1}
DEFINITIONS={
    'physical_room_count':spec(P,{'room':1},'STR','客房数量','physical rooms'),
    'reporting_days':spec(D,{'day':1},'STR','期间天数','period days'),
    'available_room_nights':spec(A,ROOM_NIGHT,'STR','可售间夜','available room nights'),
    'sold_room_nights':spec(S,ROOM_NIGHT,'STR','已售间夜','sold room nights'),
    'room_revenue':spec(R,{'CNY':1},'STR','客房收入','room revenue'),
    'occupancy_rate':spec(S/A,{},'STR','入住率','occupancy'),
    'average_daily_rate':spec(R/S,PRICE,'STR','平均房价','average daily rate'),
    'revpar':spec(R/A,PRICE,'STR','每可售间夜收入','revenue per available room'),
    'gross_sales_revenue':spec(G,{'CNY':1},'IRS','销售总额','gross sales'),
    'sales_returns':spec(T,{'CNY':1},'IRS','退货折让','returns and allowances'),
    'net_sales_revenue':spec(NET,{'CNY':1},'IRS','销售净额','net sales'),
    'cost_of_goods_sold':spec(C,{'CNY':1},'IRS','销货成本','cost of goods sold'),
    'gross_profit':spec(NET-C,{'CNY':1},'IRS','毛利','gross profit'),
    'gross_profit_margin':spec((NET-C)/NET,{},'IRS','毛利率','gross profit margin'),
    'operating_expenses':spec(O,{'CNY':1},'LOCAL','营业费用','operating expenses'),
    'operating_profit':spec(NET-C-O,{'CNY':1},'LOCAL','营业利润','operating profit'),
    'operating_profit_margin':spec((NET-C-O)/NET,{},'LOCAL','营业利润率','operating profit margin'),
    'units_sold':spec(U,{'item':1},'LOCAL','净售出商品数','net units sold'),
    'average_unit_selling_price':spec(NET/U,{'CNY':1,'item':-1},'LOCAL','平均销售单价','average unit selling price'),
    'prior_net_sales_revenue':spec(N0,{'CNY':1},'LOCAL','上年同期销售净额','prior comparable net sales'),
    'net_sales_growth_rate':spec((NET-N0)/N0,{},'LOCAL','销售净额增长率','net sales growth'),
}

ALIASES={name:name for name in DEFINITIONS}
for canonical,names in {
    'physical_room_count':['room_count','number_of_rooms','physical_rooms','total_rooms'],
    'reporting_days':['days_in_period','period_days','quarter_days'],
    'available_room_nights':['room_nights_available','available_room_nights_total','room_supply'],
    'sold_room_nights':['occupied_room_nights','room_nights_sold','rooms_sold'],
    'room_revenue':['rooms_revenue','guestroom_revenue'],
    'occupancy_rate':['occupancy','room_occupancy_rate','occupancy_ratio'],
    'average_daily_rate':['adr','average_room_rate'],
    'revpar':['revenue_per_available_room','revenue_per_available_room_night'],
    'gross_sales_revenue':['gross_sales','gross_receipts','gross_sales_amount'],
    'sales_returns':['returns_and_allowances','sales_returns_and_allowances','returns_allowances'],
    'net_sales_revenue':['net_sales','net_receipts','net_revenue'],
    'cost_of_goods_sold':['cogs','cost_of_sales','merchandise_cost'],
    'gross_profit_margin':['gross_margin','gross_margin_ratio','gross_profit_margin_ratio'],
    'operating_expenses':['opex','total_operating_expenses'],
    'operating_profit':['operating_income'],
    'operating_profit_margin':['operating_margin','operating_margin_ratio'],
    'units_sold':['net_units_sold','items_sold','merchandise_units_sold'],
    'average_unit_selling_price':['average_selling_price','average_unit_price','average_sales_price'],
    'prior_net_sales_revenue':['prior_year_net_sales','prior_year_same_quarter_net_sales','previous_comparable_net_sales'],
    'net_sales_growth_rate':['net_sales_yoy_growth_rate','revenue_growth_rate','sales_growth_rate'],
}.items():
    for name in names:ALIASES[name]=canonical


def canonical(concept):
    return ALIASES.get(str(concept).strip().lower())


def units(unit):
    if not isinstance(unit,dict):raise ValueError('UNIT_SCHEMA')
    out={}
    for key,power in unit.items():
        if key not in {'CNY','room_night','room','day','item','complaint'} or type(power)is not int or not power:
            raise ValueError('UNKNOWN_UNIT')
        parts=ROOM_NIGHT if key=='room_night' else {key:1}
        for dimension,exponent in parts.items():out[dimension]=out.get(dimension,0)+exponent*power
    return {k:v for k,v in out.items() if v}
