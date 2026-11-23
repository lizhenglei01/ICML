"""Independent numeric business worlds; no proposed formula is used here."""
from fractions import Fraction
import random


def worlds(seed=0,count=36):
    rng=random.Random(seed)
    for i in range(count):
        rooms=[1,100,250,80][i%4] if i<8 else rng.randint(20,300)
        days=92
        capacity=rooms*days
        sold=[1,capacity,max(1,capacity//2),max(1,capacity*4//5)][i%4] if i<8 else rng.randint(1,capacity)
        rate=rng.randint(100,1500)
        room_revenue=sold*rate
        gross=rng.randint(10000,1000000)
        returns=0 if i%3==0 else rng.randint(1,gross//4)
        net=gross-returns
        # Includes zero/negative margins and changing ranking/threshold statuses.
        cogs=[net,net+100,max(1,net//2),max(1,net*4//5)][i%4] if i<8 else rng.randint(1,net*6//5)
        opex=rng.randint(1,max(2,net//3));qty=rng.randint(1,1000);prior=rng.randint(1,net*2)
        roles={
            'physical_room_count':Fraction(rooms),'reporting_days':Fraction(days),
            'available_room_nights':Fraction(capacity),'sold_room_nights':Fraction(sold),
            'room_revenue':Fraction(room_revenue),'occupancy_rate':Fraction(sold,capacity),
            'average_daily_rate':Fraction(room_revenue,sold),'revpar':Fraction(room_revenue,capacity),
            'gross_sales_revenue':Fraction(gross),'sales_returns':Fraction(returns),
            'net_sales_revenue':Fraction(net),'cost_of_goods_sold':Fraction(cogs),
            'gross_profit':Fraction(net-cogs),'gross_profit_margin':Fraction(net-cogs,net),
            'operating_expenses':Fraction(opex),'operating_profit':Fraction(net-cogs-opex),
            'operating_profit_margin':Fraction(net-cogs-opex,net),
            'units_sold':Fraction(qty),'average_unit_selling_price':Fraction(net,qty),
            'prior_net_sales_revenue':Fraction(prior),'net_sales_growth_rate':Fraction(net-prior,prior)}
        yield {'roles':roles,'basis':{'physical_rooms':rooms,'period_days':days,'sold_room_nights':sold,
               'room_revenue':room_revenue,'gross_receipts':gross,'returns':returns,'cogs':cogs,
               'opex':opex,'net_units':qty,'prior_net_sales':prior}}
