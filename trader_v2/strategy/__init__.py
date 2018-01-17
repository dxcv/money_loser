# -*- coding: utf-8 -*-
import logging

import numpy as np
import talib

from trader_v2.event import *
from trader_v2.trader_object import BarData

logger = logging.getLogger("strategy")


class StrategyBase(object):
    def __init__(self, event_engine):
        self.event_engine = event_engine
        self.balance = {}

    def start(self):
        logger.info("start strategy {name}".format(name=self.__name__))

    def subscribe_depth(self, symbol):
        """
        订阅五档行情数据
        """
        event = Event(EVENT_HUOBI_SUBSCRIBE_DEPTH)
        event.dict_ = {"data": symbol}
        self.event_engine.put(event)
        self.event_engine.register(EVENT_HUOBI_DEPTH_PRE + symbol, self._on_depth)

    def _on_depth(self, event):
        depth_item = event.dict_['data']
        self.on_depth(depth_item)

    def on_depth(self, depth_item):
        """
        如果订阅了五档行情，需要实现这个方法
        :param depth_item: 
        :return: 
        """
        pass

    def subscribe_balance(self):
        """
        订阅持仓数据
        :return: 
        """
        self.event_engine.register(EVENT_HUOBI_BALANCE, self._on_balance)

    def _on_balance(self, event):
        balance = event.dict_['data']
        self.balance = balance

    def subscribe_market_trade(self, symbol):
        """
        订阅市场实时行情
        :return: 
        """
        event = Event(EVENT_HUOBI_SUBSCRIBE_TRADE)
        event.dict_ = {"data": symbol}
        self.event_engine.put(event)
        self.event_engine.register(EVENT_HUOBI_MARKET_DETAIL_PRE + symbol, self._on_market_trade)

    def _on_market_trade(self, event):
        data = event.dict_['data']
        self.on_market_trade(data)

    def on_market_trade(self, market_trade_item):
        pass

    def stop(self):
        logger.info("close strategy {name}".format(name=self.__name__))


class BarManager(object):
    """
    K线合成器，支持：
    1. 基于Tick合成1分钟K线
    2. 基于1分钟K线合成X分钟K线（X可以是2、3、5、10、15、30、60）
    """

    # ----------------------------------------------------------------------
    def __init__(self, on_bar, xmin=0, on_xmin_bar=None):
        """Constructor"""
        self.bar = None  # 1分钟K线对象
        self.onBar = on_bar  # 1分钟K线回调函数

        self.xmin_bar = None  # X分钟K线对象
        self.xmin = xmin  # X的值
        self.on_xmin_bar = on_xmin_bar  # X分钟K线的回调函数

        self.last_market_trade_item = None  # 上一个市场交易数据的缓存

    # ----------------------------------------------------------------------
    def update(self, market_trade_item):
        """TICK更新"""
        new_minute = False  # 默认不是新的一分钟

        # 尚未创建对象
        if not self.bar:
            self.bar = BarData()
            new_minute = True
        # 新的一分钟
        elif self.bar.datetime.minute != market_trade_item.datetime.minute:
            # 生成上一分钟K线的时间戳
            self.bar.datetime = self.bar.datetime.replace(second=0, microsecond=0)  # 将秒和微秒设为0

            # 推送已经结束的上一分钟K线
            self.onBar(self.bar)

            # 创建新的K线对象
            self.bar = BarData()
            new_minute = True

        # 初始化新一分钟的K线数据
        if new_minute:
            self.bar.symbol = market_trade_item.symbol

            self.bar.open = market_trade_item.price
            self.bar.high = market_trade_item.price
            self.bar.low = market_trade_item.price
        # 累加更新老一分钟的K线数据
        else:
            self.bar.high = max(self.bar.high, market_trade_item.price)
            self.bar.low = min(self.bar.low, market_trade_item.price)

        # 通用更新部分
        self.bar.close = market_trade_item.price
        self.bar.datetime = market_trade_item.datetime

        self.bar.amount += market_trade_item.amount  # 当前K线内的成交量

        # 缓存Tick
        self.last_market_trade_item = market_trade_item

    # ----------------------------------------------------------------------
    def update_bar(self, bar):
        """1分钟K线更新"""
        # 尚未创建对象
        if not self.xmin_bar:
            self.xmin_bar = BarData()

            self.xmin_bar.symbol = bar.symbol

            self.xmin_bar.open = bar.open
            self.xmin_bar.high = bar.high
            self.xmin_bar.low = bar.low

            self.xmin_bar.datetime = bar.datetime  # 以第一根分钟K线的开始时间戳作为X分钟线的时间戳
        # 累加老K线
        else:
            self.xmin_bar.high = max(self.xmin_bar.high, bar.high)
            self.xmin_bar.low = min(self.xmin_bar.low, bar.low)

        # 通用部分
        self.xmin_bar.close = bar.close
        self.xmin_bar.amount += bar.amount

        # X分钟已经走完
        if not (bar.datetime.minute + 1) % self.xmin:  # 可以用X整除
            # 生成上一X分钟K线的时间戳
            self.xmin_bar.datetime = self.xmin_bar.datetime.replace(second=0, microsecond=0)  # 将秒和微秒设为0

            # 推送
            self.on_xmin_bar(self.xmin_bar)

            # 清空老K线缓存对象
            self.xmin_bar = None


class ArrayManager(object):
    """
    K线序列管理工具，负责：
    1. K线时间序列的维护
    2. 常用技术指标的计算
    """

    # ----------------------------------------------------------------------
    def __init__(self, size=100):
        """Constructor"""
        self.count = 0  # 缓存计数
        self.size = size  # 缓存大小
        self.inited = False  # True if count>=size

        self.openArray = np.zeros(size)  # OHLC
        self.highArray = np.zeros(size)
        self.lowArray = np.zeros(size)
        self.closeArray = np.zeros(size)
        self.volumeArray = np.zeros(size)

    # ----------------------------------------------------------------------
    def update_bar(self, bar):
        """更新K线"""
        self.count += 1
        if not self.inited and self.count >= self.size:
            self.inited = True

        self.openArray[0:self.size - 1] = self.openArray[1:self.size]
        self.highArray[0:self.size - 1] = self.highArray[1:self.size]
        self.lowArray[0:self.size - 1] = self.lowArray[1:self.size]
        self.closeArray[0:self.size - 1] = self.closeArray[1:self.size]
        self.volumeArray[0:self.size - 1] = self.volumeArray[1:self.size]

        self.openArray[-1] = bar.open
        self.highArray[-1] = bar.high
        self.lowArray[-1] = bar.low
        self.closeArray[-1] = bar.close
        # 可能有点问题 怀疑是总量
        self.volumeArray[-1] = bar.amount

    # ----------------------------------------------------------------------
    @property
    def open(self):
        """获取开盘价序列"""
        return self.openArray

    # ----------------------------------------------------------------------
    @property
    def high(self):
        """获取最高价序列"""
        return self.highArray

    # ----------------------------------------------------------------------
    @property
    def low(self):
        """获取最低价序列"""
        return self.lowArray

    # ----------------------------------------------------------------------
    @property
    def close(self):
        """获取收盘价序列"""
        return self.closeArray

    # ----------------------------------------------------------------------
    @property
    def volume(self):
        """获取成交量序列"""
        return self.volumeArray

    # ----------------------------------------------------------------------
    def sma(self, n, array=False):
        """简单均线"""
        result = talib.SMA(self.close, n)
        if array:
            return result
        return result[-1]

    # ----------------------------------------------------------------------
    def std(self, n, array=False):
        """标准差"""
        result = talib.STDDEV(self.close, n)
        if array:
            return result
        return result[-1]

    # ----------------------------------------------------------------------
    def cci(self, n, array=False):
        """CCI指标"""
        result = talib.CCI(self.high, self.low, self.close, n)
        if array:
            return result
        return result[-1]

    # ----------------------------------------------------------------------
    def atr(self, n, array=False):
        """ATR指标"""
        result = talib.ATR(self.high, self.low, self.close, n)
        if array:
            return result
        return result[-1]

    # ----------------------------------------------------------------------
    def rsi(self, n, array=False):
        """RSI指标"""
        result = talib.RSI(self.close, n)
        if array:
            return result
        return result[-1]

    # ----------------------------------------------------------------------
    def macd(self, fastPeriod, slowPeriod, signalPeriod, array=False):
        """MACD指标"""
        macd, signal, hist = talib.MACD(self.close, fastPeriod,
                                        slowPeriod, signalPeriod)
        if array:
            return macd, signal, hist
        return macd[-1], signal[-1], hist[-1]

    # ----------------------------------------------------------------------
    def adx(self, n, array=False):
        """ADX指标"""
        result = talib.ADX(self.high, self.low, self.close, n)
        if array:
            return result
        return result[-1]

    # ----------------------------------------------------------------------
    def boll(self, n, dev, array=False):
        """布林通道"""
        mid = self.sma(n, array)
        std = self.std(n, array)

        up = mid + std * dev
        down = mid - std * dev

        return up, down

        # ----------------------------------------------------------------------

    def keltner(self, n, dev, array=False):
        """肯特纳通道"""
        mid = self.sma(n, array)
        atr = self.atr(n, array)

        up = mid + atr * dev
        down = mid - atr * dev

        return up, down

    # ----------------------------------------------------------------------
    def donchian(self, n, array=False):
        """唐奇安通道"""
        up = talib.MAX(self.high, n)
        down = talib.MIN(self.low, n)

        if array:
            return up, down
        return up[-1], down[-1]
