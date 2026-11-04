function pad(n) {
    return n < 10 ? '0' + n : '' + n;
}

function toDate(value) {
    if (!value) {
        return null;
    }
    const d = new Date(value);
    return isNaN(d.getTime()) ? null : d;
}

function fmtDate(value) {
    const d = toDate(value);
    if (!d) {
        return '未记录时间';
    }
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) +
        ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
}

function medsText(meds) {
    if (!meds || !meds.length) {
        return '这次没有记录用药';
    }
    const names = meds.map(function (m) { return m.name || '未命名药'; });
    return names.join('、');
}

function maskPhone(phone) {
    if (!phone || phone.length !== 11) {
        return phone || '未绑定';
    }
    return phone.slice(0, 3) + '****' + phone.slice(7);
}

function shape(record) {
    return {
        id: record.id,
        date: fmtDate(record.visit_at),
        hospital: record.hospital || '未记录医院',
        summary: record.summary || '未写明诊断',
        medsText: medsText(record.meds),
        medsCount: (record.meds || []).length,
        followUp: record.follow_up || '',
    };
}

module.exports = {
    fmtDate: fmtDate,
    medsText: medsText,
    maskPhone: maskPhone,
    shape: shape,
};
