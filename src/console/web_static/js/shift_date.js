(() => {
    const businessTimeZone = 'Asia/Shanghai';

    window.formatDutyShiftDate = value => {
        if (!value) return '日期未知';
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return '日期未知';
        return new Intl.DateTimeFormat('zh-CN', {
            timeZone: businessTimeZone,
            month: 'long',
            day: 'numeric'
        }).format(date);
    };
})();
