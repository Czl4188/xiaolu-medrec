const { request, readToken } = require('../../utils/api');
const { shape } = require('../../utils/format');

Page({
    data: {
        logged: true,
        loading: false,
        error: '',
        rows: [],
    },

    onShow() {
        this.load();
    },

    onPullDownRefresh() {
        this.load(true);
    },

    load(fromPull) {
        if (!readToken()) {
            this.setData({ logged: false, rows: [], loading: false, error: '' });
            if (fromPull) {
                wx.stopPullDownRefresh();
            }
            return;
        }
        this.setData({ logged: true, loading: true, error: '' });
        request('/api/records')
            .then((list) => {
                this.setData({ rows: (list || []).map(shape), loading: false });
            })
            .catch((err) => {
                this.setData({ loading: false, error: err.message || '加载失败' });
            })
            .then(() => {
                if (fromPull) {
                    wx.stopPullDownRefresh();
                }
            });
    },

    openItem(e) {
        const id = e.currentTarget.dataset.id;
        wx.navigateTo({ url: '/pages/detail/detail?id=' + id });
    },

    goLogin() {
        wx.navigateTo({ url: '/pages/login/login' });
    },

    retry() {
        this.load();
    },
});
