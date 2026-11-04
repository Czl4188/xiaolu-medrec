const { request } = require('../../utils/api');
const { fmtDate } = require('../../utils/format');

Page({
    data: {
        loading: true,
        error: '',
        record: null,
    },

    onLoad(query) {
        this.id = query && query.id;
        if (!this.id) {
            this.setData({ loading: false, error: '没有拿到记录编号' });
            return;
        }
        this.load();
    },

    load() {
        this.setData({ loading: true, error: '' });
        request('/api/records/' + this.id)
            .then((r) => {
                this.setData({ loading: false, record: this.shape(r) });
            })
            .catch((err) => {
                this.setData({ loading: false, error: err.message || '读取失败' });
            });
    },

    shape(r) {
        const meds = (r.meds || []).map(function (m) {
            const parts = [m.dose, m.freq, m.timing, m.days ? '共 ' + m.days + ' 天' : ''];
            return {
                name: m.name || '未命名药',
                detail: parts.filter(function (x) { return !!x; }).join(' · ') || '用法未记录',
            };
        });
        return {
            date: fmtDate(r.visit_at),
            hospital: r.hospital || '未记录医院',
            summary: r.summary || '未写明诊断',
            meds: meds,
            followUp: r.follow_up || '未记录',
            notes: r.notes || [],
            conditions: r.conditions || [],
            allergy: r.allergy || '',
            allergyDenied: !!r.allergy_denied,
            transcript: r.transcript || '',
        };
    },

    copyTranscript() {
        if (!this.data.record || !this.data.record.transcript) {
            return;
        }
        wx.setClipboardData({ data: this.data.record.transcript });
    },

    goBack() {
        wx.navigateBack({ delta: 1 });
    },
});
