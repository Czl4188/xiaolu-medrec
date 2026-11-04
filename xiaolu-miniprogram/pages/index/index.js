const { request, readToken, readUser } = require('../../utils/api');
const { shape } = require('../../utils/format');

Page({
    data: {
        logged: false,
        user: null,
        loading: false,
        error: '',
        total: 0,
        last: null,
    },

    onShow() {
        this.refresh();
    },

    refresh() {
        const token = readToken();
        if (!token) {
            this.setData({ logged: false, user: null, last: null, error: '', total: 0 });
            return;
        }
        this.setData({ logged: true, user: readUser() || {}, loading: true, error: '' });
        request('/api/records')
            .then((list) => {
                const rows = (list || []).map(shape);
                this.setData({
                    total: rows.length,
                    last: rows.length ? rows[0] : null,
                    loading: false,
                });
            })
            .catch((err) => {
                this.setData({ loading: false, error: err.message || '档案加载失败' });
            });
    },

    goLogin() {
        wx.navigateTo({ url: '/pages/login/login' });
    },

    goRecords() {
        wx.switchTab({ url: '/pages/records/records' });
    },

    goAsk() {
        wx.switchTab({ url: '/pages/ask/ask' });
    },

    goMine() {
        wx.switchTab({ url: '/pages/mine/mine' });
    },

    openLast() {
        if (!this.data.last) {
            return;
        }
        wx.navigateTo({ url: '/pages/detail/detail?id=' + this.data.last.id });
    },
});
