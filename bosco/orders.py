"""Shared command validation; prices follow Gigamoney's directional cent rounding."""
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, localcontext
import re
import logging
from .diagnostics import command_context

class OrderAutomation:
    @staticmethod
    def normalize_symbol(value):
        value = value.strip().upper()
        if not re.fullmatch(r'[A-Z0-9.\-]{1,64}', value):
            raise ValueError('Invalid stock symbol.')
        return value

    @staticmethod
    def normalize_side(value):
        value = value.strip().upper()
        if value not in {'BUY', 'SELL'}:
            raise ValueError('Side must be Buy or Sell.')
        return value.title()

    @staticmethod
    def positive_number(value, name):
        try:
            number = Decimal(str(value))
        except InvalidOperation:
            raise ValueError(f'{name} must be a number.') from None
        if not number.is_finite() or number <= 0:
            raise ValueError(f'{name} must be positive and finite.')
        return format(number, 'f')

    @classmethod
    def order_price(cls, value, side, name='price'):
        requested = cls.positive_number(value, name)
        parsed = Decimal(requested)
        side = cls.normalize_side(side)
        if parsed.as_tuple().exponent >= -2:
            return requested
        with localcontext() as ctx:
            ctx.prec = max(ctx.prec, len(parsed.as_tuple().digits) + 2)
            rounded = parsed.quantize(Decimal('.01'), rounding=ROUND_CEILING if side == 'Buy' else ROUND_FLOOR)
        if rounded <= 0:
            raise ValueError('Sell price rounds down to zero.')
        effective = format(rounded, 'f')
        logging.getLogger('bosco').info('Order price rounded command_id=%s field=%s side=%s requested=%s effective=%s.',
                                      command_context.get(), name, side, requested, effective)
        return effective
