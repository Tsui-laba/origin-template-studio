using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using System.IO.Compression;
using System.Xml;

internal sealed class SlotOption
{
    public string Id { get; private set; }
    public string Title { get; private set; }
    public string LabelMatch { get; private set; }
    public string DefaultName { get; private set; }

    public SlotOption(string id, string title, string labelMatch, string defaultName)
    {
        Id = id;
        Title = title;
        LabelMatch = labelMatch;
        DefaultName = defaultName;
    }

    public bool UsesSampleName
    {
        get
        {
            return true;
        }
    }
}

internal sealed class TemplateOption
{
    public string Id { get; private set; }
    public string Slide { get; private set; }
    public IList<SlotOption> Slots { get; private set; }
    public bool VariableSlots { get; private set; }
    public int MinSlots { get; private set; }
    public int DefaultSlots { get; private set; }

    public TemplateOption(
        string id,
        string slide,
        IList<SlotOption> slots,
        bool variableSlots,
        int minSlots,
        int defaultSlots
    )
    {
        Id = id;
        Slide = slide;
        Slots = slots ?? new List<SlotOption>();
        VariableSlots = variableSlots && Slots.Count > 0;
        MinSlots = Math.Max(1, Math.Min(minSlots, Math.Max(1, Slots.Count)));
        DefaultSlots = Math.Max(MinSlots, Math.Min(defaultSlots, Math.Max(1, Slots.Count)));
    }

    public IList<SlotOption> UiSlots
    {
        get
        {
            if (Slots.Count > 0)
            {
                return Slots;
            }
            return new List<SlotOption>
            {
                new SlotOption("data", "資料", "", "")
            };
        }
    }

    public override string ToString()
    {
        return "第 " + Slide + " 頁  ·  " + Id;
    }
}

internal sealed class SlotSelection
{
    public string Id;
    public string DataPath;
    public string SampleName;
}

internal sealed class SlotDraft
{
    public string DataPath;
    public string SampleName;
}

internal sealed class RenderRequest
{
    public string TemplateId;
    public IList<SlotSelection> Slots;
    public int SlotCount;
    public string OutputPath;
    public bool HiddenOrigin;
    public decimal ElectrodeWeightMg;
    public decimal FoilWeightMg;
    public string ExcelSheet;
    public string CapacityUnit;
}

internal sealed class RenderResult
{
    public bool Success;
    public string Message;
    public string OutputPath;
}

internal sealed class OriginPlotterForm : Form
{
    private const int SlotTitleColumn = 0;
    private const int DataPathColumn = 1;
    private const int BrowseColumn = 2;
    private const int SampleNameColumn = 3;

    private readonly string appDirectory;
    private readonly ComboBox templateBox;
    private readonly DataGridView slotGrid;
    private readonly TextBox outputBox;
    private readonly Button outputBrowseButton;
    private readonly Button generateButton;
    private readonly Button openButton;
    private readonly CheckBox hiddenCheck;
    private readonly ProgressBar progress;
    private readonly Label statusLabel;
    private readonly Label slotHintLabel;
    private readonly Label slotCountLabel;
    private readonly NumericUpDown slotCountBox;
    private readonly BackgroundWorker worker;
    private readonly Panel batteryPanel;
    private readonly NumericUpDown electrodeWeightBox;
    private readonly NumericUpDown foilWeightBox;
    private readonly ComboBox batterySheetBox;
    private readonly ComboBox capacityUnitBox;
    private readonly Dictionary<string, Dictionary<string, SlotDraft>> drafts;
    private readonly Dictionary<string, int> slotCounts;
    private bool updatingSlotCount;
    private string displayedTemplateId = "";
    private string lastAutomaticOutput = "";
    private string lastOutput = "";

    public OriginPlotterForm()
    {
        string baseDirectory = AppDomain.CurrentDomain.BaseDirectory;
        appDirectory = Path.Combine(baseDirectory, "python_origin_plotter");
        drafts = new Dictionary<string, Dictionary<string, SlotDraft>>(StringComparer.OrdinalIgnoreCase);
        slotCounts = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);

        Text = "Origin 模板出圖中心";
        StartPosition = FormStartPosition.CenterScreen;
        ClientSize = new Size(960, 800);
        MinimumSize = new Size(880, 740);
        BackColor = Color.White;
        Font = new Font("Microsoft JhengHei UI", 10F, FontStyle.Regular, GraphicsUnit.Point);
        AutoScaleMode = AutoScaleMode.Dpi;

        var title = new Label();
        title.Text = "Origin 模板專案產生器";
        title.Font = new Font("Microsoft JhengHei UI", 21F, FontStyle.Bold, GraphicsUnit.Point);
        title.AutoSize = true;
        title.Location = new Point(30, 22);
        Controls.Add(title);

        var subtitle = new Label();
        subtitle.Text = "選擇圖型與合併數量，再依畫面順序指定資料與名稱；XRD 固定 10–80°，其他座標軸依模板自動調整。";
        subtitle.ForeColor = Color.FromArgb(85, 85, 85);
        subtitle.AutoSize = true;
        subtitle.Location = new Point(33, 67);
        Controls.Add(subtitle);

        var settings = new GroupBox();
        settings.Text = "  出圖設定  ";
        settings.Location = new Point(30, 101);
        settings.Size = new Size(900, 556);
        settings.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
        Controls.Add(settings);

        AddStepLabel(settings, "1", 31);
        AddFieldLabel(settings, "選擇圖型", 31);
        templateBox = new ComboBox();
        templateBox.Name = "templateBox";
        templateBox.DropDownStyle = ComboBoxStyle.DropDownList;
        templateBox.Location = new Point(176, 27);
        templateBox.Size = new Size(685, 30);
        templateBox.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
        foreach (TemplateOption option in LoadTemplates())
        {
            templateBox.Items.Add(option);
        }
        templateBox.SelectedIndexChanged += TemplateChanged;
        settings.Controls.Add(templateBox);

        AddStepLabel(settings, "2", 82);
        AddFieldLabel(settings, "依序選擇資料與名稱", 82);
        slotHintLabel = new Label();
        slotHintLabel.ForeColor = Color.FromArgb(90, 90, 90);
        slotHintLabel.AutoSize = false;
        slotHintLabel.Location = new Point(262, 84);
        slotHintLabel.Size = new Size(330, 25);
        settings.Controls.Add(slotHintLabel);

        slotCountLabel = new Label();
        slotCountLabel.Name = "slotCountLabel";
        slotCountLabel.Text = "合併資料數量";
        slotCountLabel.AutoSize = true;
        slotCountLabel.Location = new Point(648, 84);
        slotCountLabel.Visible = false;
        settings.Controls.Add(slotCountLabel);

        slotCountBox = new NumericUpDown();
        slotCountBox.Name = "slotCountBox";
        slotCountBox.Minimum = 1;
        slotCountBox.Maximum = 6;
        slotCountBox.Value = 1;
        slotCountBox.TextAlign = HorizontalAlignment.Center;
        slotCountBox.Location = new Point(785, 80);
        slotCountBox.Size = new Size(76, 30);
        slotCountBox.Visible = false;
        slotCountBox.ValueChanged += SlotCountChanged;
        settings.Controls.Add(slotCountBox);

        slotGrid = BuildSlotGrid();
        slotGrid.Location = new Point(25, 111);
        slotGrid.Size = new Size(836, 280);
        slotGrid.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
        slotGrid.CellContentClick += BrowseSlotData;
        settings.Controls.Add(slotGrid);
        slotGrid.CellEndEdit += delegate(object sender, DataGridViewCellEventArgs e) {
            if (e.RowIndex >= 0 && e.ColumnIndex == DataPathColumn)
                SetFileSampleName(slotGrid.Rows[e.RowIndex]);
            RefreshBatterySheets();
        };

        batteryPanel = new Panel();
        batteryPanel.Location = new Point(25, 195);
        batteryPanel.Size = new Size(836, 194);
        batteryPanel.Visible = false;
        settings.Controls.Add(batteryPanel);
        batteryPanel.Controls.Add(new Label { Text = "工作表／通道", Location = new Point(8, 12), AutoSize = true });
        batterySheetBox = new ComboBox { Location = new Point(145, 8), Size = new Size(235, 30), DropDownStyle = ComboBoxStyle.DropDownList };
        batteryPanel.Controls.Add(batterySheetBox);
        batteryPanel.Controls.Add(new Label { Text = "容量單位", Location = new Point(420, 12), AutoSize = true });
        capacityUnitBox = new ComboBox { Location = new Point(520, 8), Size = new Size(110, 30), DropDownStyle = ComboBoxStyle.DropDownList };
        capacityUnitBox.Items.AddRange(new object[] { "Ah", "mAh" });
        capacityUnitBox.SelectedIndex = 0;
        batteryPanel.Controls.Add(capacityUnitBox);
        batteryPanel.Controls.Add(new Label { Text = "極片重量 (mg)", Location = new Point(8, 60), AutoSize = true });
        electrodeWeightBox = new NumericUpDown { Location = new Point(145, 55), Size = new Size(160, 30), DecimalPlaces = 4, Maximum = 100000, Increment = 0.1M };
        batteryPanel.Controls.Add(electrodeWeightBox);
        batteryPanel.Controls.Add(new Label { Text = "鋁箔重量 (mg)", Location = new Point(420, 60), AutoSize = true });
        foilWeightBox = new NumericUpDown { Location = new Point(560, 55), Size = new Size(160, 30), DecimalPlaces = 4, Maximum = 100000, Increment = 0.1M, Value = 7.4M };
        batteryPanel.Controls.Add(foilWeightBox);
        batteryPanel.Controls.Add(new Label { Text = "前三圈：Current > 0 充電，< 0 放電；0 電流資料排除。\r\nX：capacity ÷ 活性物質重量 (mAh/g)；Y：Voltage (V)。\r\n活性重量 = (極片 − 鋁箔) × 0.8；mg 轉 g、Ah 轉 mAh。", Location = new Point(8, 105), Size = new Size(815, 82), ForeColor = Color.FromArgb(85, 85, 85) });

        AddStepLabel(settings, "3", 414);
        AddFieldLabel(settings, "輸出專案位置", 414);
        outputBox = new TextBox();
        outputBox.Name = "outputBox";
        outputBox.Location = new Point(176, 410);
        outputBox.Size = new Size(584, 30);
        outputBox.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
        settings.Controls.Add(outputBox);

        outputBrowseButton = new Button();
        outputBrowseButton.Name = "outputBrowseButton";
        outputBrowseButton.Text = "瀏覽…";
        outputBrowseButton.Location = new Point(772, 408);
        outputBrowseButton.Size = new Size(89, 33);
        outputBrowseButton.Anchor = AnchorStyles.Top | AnchorStyles.Right;
        outputBrowseButton.Click += BrowseOutput;
        settings.Controls.Add(outputBrowseButton);

        hiddenCheck = new CheckBox();
        hiddenCheck.Name = "hiddenCheck";
        hiddenCheck.Text = "在背景執行 Origin（建議）";
        hiddenCheck.Checked = true;
        hiddenCheck.AutoSize = true;
        hiddenCheck.Location = new Point(176, 458);
        settings.Controls.Add(hiddenCheck);

        var hint = new Label();
        hint.Text = "充放電：單一資料檔自動擷取前三圈，輸入極片及鋁箔重量（mg）。\r\nXRD 支援儀器及 PDF 圖譜 TXT；其他一般資料使用 X／Y；C-RATE 依 Y 點數產生 1–N。";
        hint.ForeColor = Color.FromArgb(105, 105, 105);
        hint.AutoSize = false;
        hint.Location = new Point(176, 490);
        hint.Size = new Size(685, 47);
        settings.Controls.Add(hint);

        progress = new ProgressBar();
        progress.Style = ProgressBarStyle.Marquee;
        progress.MarqueeAnimationSpeed = 22;
        progress.Location = new Point(33, 691);
        progress.Size = new Size(240, 18);
        progress.Visible = false;
        Controls.Add(progress);

        openButton = new Button();
        openButton.Name = "openButton";
        openButton.Text = "開啟專案";
        openButton.Enabled = false;
        openButton.Location = new Point(702, 680);
        openButton.Size = new Size(105, 39);
        openButton.Anchor = AnchorStyles.Top | AnchorStyles.Right;
        openButton.Click += OpenOutput;
        Controls.Add(openButton);

        generateButton = new Button();
        generateButton.Name = "generateButton";
        generateButton.Text = "儲存專案";
        generateButton.BackColor = Color.FromArgb(47, 111, 237);
        generateButton.ForeColor = Color.White;
        generateButton.FlatStyle = FlatStyle.Flat;
        generateButton.FlatAppearance.BorderSize = 0;
        generateButton.Font = new Font("Microsoft JhengHei UI", 10.5F, FontStyle.Bold, GraphicsUnit.Point);
        generateButton.Location = new Point(818, 680);
        generateButton.Size = new Size(112, 39);
        generateButton.Anchor = AnchorStyles.Top | AnchorStyles.Right;
        generateButton.Click += StartRender;
        Controls.Add(generateButton);

        var statusCaption = new Label();
        statusCaption.Text = "狀態：";
        statusCaption.ForeColor = Color.FromArgb(90, 90, 90);
        statusCaption.AutoSize = true;
        statusCaption.Location = new Point(33, 738);
        Controls.Add(statusCaption);

        statusLabel = new Label();
        statusLabel.Name = "statusLabel";
        statusLabel.Text = "準備完成";
        statusLabel.AutoEllipsis = true;
        statusLabel.Location = new Point(84, 738);
        statusLabel.Size = new Size(830, 24);
        statusLabel.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
        Controls.Add(statusLabel);

        var reminder = new Label();
        reminder.Text = "執行前請先儲存並關閉手動開啟的 Origin；Python 與 Origin 請使用相同權限。";
        reminder.ForeColor = Color.FromArgb(105, 105, 105);
        reminder.AutoSize = true;
        reminder.Location = new Point(33, 768);
        Controls.Add(reminder);

        worker = new BackgroundWorker();
        worker.DoWork += RenderInBackground;
        worker.RunWorkerCompleted += RenderCompleted;
        FormClosing += PreventCloseWhileRendering;

        if (templateBox.Items.Count > 0)
        {
            templateBox.SelectedIndex = 0;
        }
    }

    private static void AddStepLabel(Control parent, string text, int y)
    {
        var badge = new Label();
        badge.Text = text;
        badge.TextAlign = ContentAlignment.MiddleCenter;
        badge.BackColor = Color.FromArgb(47, 111, 237);
        badge.ForeColor = Color.White;
        badge.Font = new Font("Microsoft JhengHei UI", 10F, FontStyle.Bold, GraphicsUnit.Point);
        badge.Location = new Point(25, y - 2);
        badge.Size = new Size(28, 28);
        parent.Controls.Add(badge);
    }

    private static void AddFieldLabel(Control parent, string text, int y)
    {
        var label = new Label();
        label.Text = text;
        label.AutoSize = true;
        label.Location = new Point(66, y + 2);
        parent.Controls.Add(label);
    }

    private static DataGridView BuildSlotGrid()
    {
        var grid = new DataGridView();
        grid.Name = "slotGrid";
        grid.AllowUserToAddRows = false;
        grid.AllowUserToDeleteRows = false;
        grid.AllowUserToResizeRows = false;
        grid.AutoGenerateColumns = false;
        grid.BackgroundColor = Color.White;
        grid.BorderStyle = BorderStyle.FixedSingle;
        grid.CellBorderStyle = DataGridViewCellBorderStyle.SingleHorizontal;
        grid.ColumnHeadersBorderStyle = DataGridViewHeaderBorderStyle.Single;
        grid.ColumnHeadersHeight = 32;
        grid.ColumnHeadersHeightSizeMode = DataGridViewColumnHeadersHeightSizeMode.DisableResizing;
        grid.EnableHeadersVisualStyles = false;
        grid.ColumnHeadersDefaultCellStyle.BackColor = Color.FromArgb(242, 245, 250);
        grid.ColumnHeadersDefaultCellStyle.Font = new Font("Microsoft JhengHei UI", 9.5F, FontStyle.Bold);
        grid.RowHeadersVisible = false;
        grid.RowTemplate.Height = 40;
        grid.SelectionMode = DataGridViewSelectionMode.CellSelect;
        grid.MultiSelect = false;

        var titleColumn = new DataGridViewTextBoxColumn();
        titleColumn.HeaderText = "圖中位置";
        titleColumn.ReadOnly = true;
        titleColumn.SortMode = DataGridViewColumnSortMode.NotSortable;
        titleColumn.Width = 170;
        grid.Columns.Add(titleColumn);

        var dataColumn = new DataGridViewTextBoxColumn();
        dataColumn.HeaderText = "資料位置";
        dataColumn.SortMode = DataGridViewColumnSortMode.NotSortable;
        dataColumn.AutoSizeMode = DataGridViewAutoSizeColumnMode.Fill;
        dataColumn.MinimumWidth = 220;
        grid.Columns.Add(dataColumn);

        var browseColumn = new DataGridViewButtonColumn();
        browseColumn.HeaderText = "選檔";
        browseColumn.Text = "瀏覽…";
        browseColumn.UseColumnTextForButtonValue = true;
        browseColumn.Width = 76;
        browseColumn.FlatStyle = FlatStyle.Standard;
        grid.Columns.Add(browseColumn);

        var sampleColumn = new DataGridViewTextBoxColumn();
        sampleColumn.HeaderText = "樣品名稱（可修改）";
        sampleColumn.SortMode = DataGridViewColumnSortMode.NotSortable;
        sampleColumn.Width = 190;
        grid.Columns.Add(sampleColumn);

        return grid;
    }

    private IList<TemplateOption> LoadTemplates()
    {
        var result = new List<TemplateOption>();
        string configPath = Path.Combine(appDirectory, "template_configs.json");
        try
        {
            var serializer = new JavaScriptSerializer();
            var root = (Dictionary<string, object>)serializer.DeserializeObject(
                File.ReadAllText(configPath, Encoding.UTF8)
            );
            var rows = (object[])root["templates"];
            foreach (object raw in rows)
            {
                var row = (Dictionary<string, object>)raw;
                var slots = new List<SlotOption>();
                object rawSlots;
                if (row.TryGetValue("ui_slots", out rawSlots) && rawSlots is object[])
                {
                    foreach (object rawSlot in (object[])rawSlots)
                    {
                        var slot = (Dictionary<string, object>)rawSlot;
                        slots.Add(new SlotOption(
                            Value(slot, "id"),
                            Value(slot, "title"),
                            Value(slot, "label_match"),
                            Value(slot, "default_name")
                        ));
                    }
                }
                result.Add(new TemplateOption(
                    Value(row, "id"),
                    Value(row, "slide"),
                    slots,
                    BoolValue(row, "ui_variable", false),
                    IntValue(row, "ui_min_slots", 1),
                    IntValue(row, "ui_default_slots", Math.Max(1, slots.Count))
                ));
            }
        }
        catch
        {
            return FallbackTemplates();
        }
        return result;
    }

    private static string Value(Dictionary<string, object> row, string key)
    {
        object value;
        return row.TryGetValue(key, out value) ? Convert.ToString(value) : "";
    }

    private static bool BoolValue(
        Dictionary<string, object> row,
        string key,
        bool fallback
    )
    {
        object value;
        if (!row.TryGetValue(key, out value) || value == null)
        {
            return fallback;
        }
        try
        {
            return Convert.ToBoolean(value);
        }
        catch
        {
            return fallback;
        }
    }

    private static int IntValue(
        Dictionary<string, object> row,
        string key,
        int fallback
    )
    {
        object value;
        if (!row.TryGetValue(key, out value) || value == null)
        {
            return fallback;
        }
        int result;
        return Int32.TryParse(Convert.ToString(value), out result) ? result : fallback;
    }

    private static IList<TemplateOption> FallbackTemplates()
    {
        string[] ids = {
            "XRD1", "XRD2", "XRD3", "CHARGE_AND_DISCHARGE", "C_RATE",
            "CYCLING", "CV_TEST", "EXCITATION", "EMISSION", "FTIR", "PARTICLE_SIZE"
        };
        var result = new List<TemplateOption>();
        for (int index = 0; index < ids.Length; index++)
        {
            IList<SlotOption> slots = new List<SlotOption>();
            int count = 1;
            bool variable = false;
            if (ids[index] == "XRD1")
            {
                slots.Add(new SlotOption("top_sample", "上欄｜樣品圖譜", "SAMPLE", "SAMPLE"));
                slots.Add(new SlotOption("middle_reference", "中欄｜參考圖譜 1", "Yttrium oxide", "Yttrium oxide"));
                slots.Add(new SlotOption("bottom_reference", "下欄｜參考圖譜 2", "Yttrium", "Yttrium"));
            }
            else
            {
                if (ids[index] == "XRD2") count = 4;
                else if (ids[index] == "XRD3") count = 2;
                else if (ids[index] == "EXCITATION") { count = 5; variable = true; }
                else if (ids[index] == "EMISSION") { count = 4; variable = true; }
                else if (ids[index] == "FTIR") { count = 6; variable = true; }
                else if (
                    ids[index] == "CHARGE_AND_DISCHARGE"
                    || ids[index] == "C_RATE"
                    || ids[index] == "CYCLING"
                    || ids[index] == "CV_TEST"
                )
                {
                    count = 3;
                    variable = true;
                }
                for (int slotIndex = 1; slotIndex <= count; slotIndex++)
                {
                    slots.Add(new SlotOption(
                        "series_" + slotIndex,
                        count == 1 ? "資料" : "資料 " + slotIndex,
                        "",
                        count == 1 && ids[index] == "PARTICLE_SIZE"
                            ? ""
                            : "Sample " + slotIndex
                    ));
                }
            }
            result.Add(new TemplateOption(
                ids[index],
                (index + 1).ToString(),
                slots,
                variable,
                1,
                Math.Max(1, slots.Count)
            ));
        }
        return result;
    }

    private TemplateOption SelectedTemplate
    {
        get { return templateBox.SelectedItem as TemplateOption; }
    }

    private void TemplateChanged(object sender, EventArgs e)
    {
        CaptureCurrentDraft();
        ConfigureSlotCount();
        RebuildSlotRows();
        SuggestOutput(false);
    }

    private void ConfigureSlotCount()
    {
        TemplateOption template = SelectedTemplate;
        updatingSlotCount = true;
        try
        {
            bool visible = template != null && template.VariableSlots;
            slotCountLabel.Visible = visible;
            slotCountBox.Visible = visible;
            if (!visible)
            {
                return;
            }
            slotCountBox.Minimum = template.MinSlots;
            slotCountBox.Maximum = Math.Max(template.MinSlots, template.Slots.Count);
            int saved;
            if (!slotCounts.TryGetValue(template.Id, out saved))
            {
                saved = template.DefaultSlots;
            }
            saved = Math.Max(
                Convert.ToInt32(slotCountBox.Minimum),
                Math.Min(saved, Convert.ToInt32(slotCountBox.Maximum))
            );
            slotCountBox.Value = saved;
            slotCounts[template.Id] = saved;
        }
        finally
        {
            updatingSlotCount = false;
        }
    }

    private void SlotCountChanged(object sender, EventArgs e)
    {
        if (updatingSlotCount)
        {
            return;
        }
        TemplateOption template = SelectedTemplate;
        if (template == null || !template.VariableSlots)
        {
            return;
        }
        CaptureCurrentDraft();
        slotCounts[template.Id] = Convert.ToInt32(slotCountBox.Value);
        RebuildSlotRows();
        SuggestOutput(false);
    }

    private void CaptureCurrentDraft()
    {
        if (String.IsNullOrWhiteSpace(displayedTemplateId))
        {
            return;
        }
        Dictionary<string, SlotDraft> existing;
        drafts.TryGetValue(displayedTemplateId, out existing);
        var templateDraft = existing == null
            ? new Dictionary<string, SlotDraft>(StringComparer.OrdinalIgnoreCase)
            : new Dictionary<string, SlotDraft>(existing, StringComparer.OrdinalIgnoreCase);
        foreach (DataGridViewRow row in slotGrid.Rows)
        {
            var slot = row.Tag as SlotOption;
            if (slot == null)
            {
                continue;
            }
            templateDraft[slot.Id] = new SlotDraft
            {
                DataPath = CellText(row, DataPathColumn),
                SampleName = CellText(row, SampleNameColumn)
            };
        }
        drafts[displayedTemplateId] = templateDraft;
    }

    private void RebuildSlotRows()
    {
        slotGrid.EndEdit();
        slotGrid.Rows.Clear();
        TemplateOption template = SelectedTemplate;
        if (template == null)
        {
            displayedTemplateId = "";
            slotHintLabel.Text = "";
            return;
        }

        Dictionary<string, SlotDraft> templateDraft;
        drafts.TryGetValue(template.Id, out templateDraft);
        IList<SlotOption> availableSlots = template.UiSlots;
        int activeCount = template.VariableSlots
            ? Math.Min(Convert.ToInt32(slotCountBox.Value), availableSlots.Count)
            : availableSlots.Count;
        for (int slotIndex = 0; slotIndex < activeCount; slotIndex++)
        {
            SlotOption slot = availableSlots[slotIndex];
            SlotDraft saved = null;
            if (templateDraft != null)
            {
                templateDraft.TryGetValue(slot.Id, out saved);
            }
            string dataPath = saved == null ? "" : saved.DataPath;
            string sampleName = saved == null ? "" : saved.SampleName;
            if (String.IsNullOrWhiteSpace(sampleName) && !String.IsNullOrWhiteSpace(dataPath))
                sampleName = Path.GetFileNameWithoutExtension(dataPath);
            int rowIndex = slotGrid.Rows.Add(slot.Title, dataPath, "瀏覽…", sampleName);
            DataGridViewRow gridRow = slotGrid.Rows[rowIndex];
            gridRow.Tag = slot;
            gridRow.Cells[SlotTitleColumn].Style.BackColor = Color.FromArgb(248, 249, 251);
            if (!slot.UsesSampleName)
            {
                gridRow.Cells[SampleNameColumn].ReadOnly = true;
                gridRow.Cells[SampleNameColumn].Value = "";
                gridRow.Cells[SampleNameColumn].Style.BackColor = Color.FromArgb(242, 242, 242);
            }
        }
        displayedTemplateId = template.Id;
        bool battery = template.Id == "CHARGE_AND_DISCHARGE";
        batteryPanel.Visible = battery;
        slotGrid.Height = battery ? 76 : 280;
        if (battery) RefreshBatterySheets();
        if (template.VariableSlots)
        {
            slotHintLabel.Text = "可選 " + template.MinSlots + "–" + template.Slots.Count
                + " 份，目前合併 " + activeCount + " 份";
            statusLabel.Text = "已選 " + template.Id + "：請填完 " + activeCount + " 列資料與名稱。";
        }
        else
        {
            slotHintLabel.Text = activeCount == 1
                ? "此圖需要 1 份資料"
                : "此圖固定需要 " + activeCount + " 份資料";
            statusLabel.Text = "已選 " + template.Id + "：請填完 " + activeCount + " 列資料。";
        }
    }

    private void SetFileSampleName(DataGridViewRow row)
    {
        string path = CellText(row, DataPathColumn).Trim();
        row.Cells[SampleNameColumn].Value = String.IsNullOrWhiteSpace(path) ? "" : Path.GetFileNameWithoutExtension(path);
    }

    private void BrowseSlotData(object sender, DataGridViewCellEventArgs e)
    {
        if (e.RowIndex < 0 || e.ColumnIndex != BrowseColumn)
        {
            return;
        }
        DataGridViewRow row = slotGrid.Rows[e.RowIndex];
        string currentPath = CellText(row, DataPathColumn);
        using (var dialog = new OpenFileDialog())
        {
            var slot = row.Tag as SlotOption;
            dialog.Title = slot == null ? "選擇輸入資料" : "選擇「" + slot.Title + "」的資料";
            dialog.Filter = "支援的資料檔|*.csv;*.tsv;*.txt;*.dat;*.xlsx;*.xls;*.json|Excel|*.xlsx;*.xls|CSV|*.csv|所有檔案|*.*";
            dialog.FilterIndex = 1;
            if (File.Exists(currentPath))
            {
                dialog.InitialDirectory = Path.GetDirectoryName(currentPath);
                dialog.FileName = Path.GetFileName(currentPath);
            }
            else
            {
                string examples = Path.Combine(appDirectory, "examples", "template_inputs");
                if (Directory.Exists(examples))
                {
                    dialog.InitialDirectory = examples;
                }
            }
            if (dialog.ShowDialog(this) == DialogResult.OK)
            {
                row.Cells[DataPathColumn].Value = dialog.FileName;
                SetFileSampleName(row);
                RefreshBatterySheets();
                CaptureCurrentDraft();
                SuggestOutput(e.RowIndex == 0);
                statusLabel.Text = "已選擇「" + CellText(row, SlotTitleColumn) + "」資料。";
            }
        }
    }

    private void BrowseOutput(object sender, EventArgs e)
    {
        using (var dialog = new SaveFileDialog())
        {
            dialog.Title = "選擇輸出專案位置";
            dialog.Filter = "Origin 專案|*.opju";
            dialog.FilterIndex = 1;
            dialog.DefaultExt = "opju";
            dialog.AddExtension = true;
            if (!String.IsNullOrWhiteSpace(outputBox.Text))
            {
                string directory = Path.GetDirectoryName(outputBox.Text);
                if (!String.IsNullOrWhiteSpace(directory) && Directory.Exists(directory))
                {
                    dialog.InitialDirectory = directory;
                }
                dialog.FileName = Path.GetFileName(outputBox.Text);
            }
            if (dialog.ShowDialog(this) == DialogResult.OK)
            {
                outputBox.Text = dialog.FileName;
                lastAutomaticOutput = "";
            }
        }
    }

    private string FirstDataPath()
    {
        foreach (DataGridViewRow row in slotGrid.Rows)
        {
            string path = CellText(row, DataPathColumn);
            if (!String.IsNullOrWhiteSpace(path))
            {
                return path;
            }
        }
        return "";
    }

    private void SuggestOutput(bool force)
    {
        TemplateOption template = SelectedTemplate;
        string firstData = FirstDataPath();
        if (template == null)
        {
            return;
        }
        string directory = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
        string stem = String.IsNullOrWhiteSpace(firstData) ? "Origin" : Path.GetFileNameWithoutExtension(firstData);
        if (String.IsNullOrWhiteSpace(directory) || String.IsNullOrWhiteSpace(stem))
        {
            return;
        }
        string suggested = Path.Combine(directory, stem + "_" + template.Id + ".opju");
        if (force || String.IsNullOrWhiteSpace(outputBox.Text) || outputBox.Text == lastAutomaticOutput)
        {
            outputBox.Text = suggested;
            lastAutomaticOutput = suggested;
        }
    }

    private void RefreshBatterySheets()
    {
        if (SelectedTemplate == null || SelectedTemplate.Id != "CHARGE_AND_DISCHARGE" || slotGrid.Rows.Count == 0) return;
        string path = CellText(slotGrid.Rows[0], DataPathColumn);
        string selected = batterySheetBox.SelectedItem == null ? "" : Convert.ToString(batterySheetBox.SelectedItem);
        batterySheetBox.Items.Clear();
        if (File.Exists(path) && String.Equals(Path.GetExtension(path), ".xlsx", StringComparison.OrdinalIgnoreCase))
        {
            try
            {
                using (var input = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
                using (var archive = new ZipArchive(input, ZipArchiveMode.Read))
                using (var stream = archive.GetEntry("xl/workbook.xml").Open())
                {
                    var xml = new XmlDocument();
                    xml.Load(stream);
                    foreach (XmlNode node in xml.SelectNodes("//*[local-name()='sheet']"))
                        batterySheetBox.Items.Add(node.Attributes["name"].Value);
                }
            }
            catch (Exception ex)
            {
                statusLabel.Text = "無法讀取工作表名稱：" + ex.Message;
            }
        }
        if (batterySheetBox.Items.Contains(selected)) batterySheetBox.SelectedItem = selected;
        else if (batterySheetBox.Items.Count > 0) batterySheetBox.SelectedIndex = 0;
    }

    private bool TryBuildRequest(out RenderRequest request)
    {
        request = null;
        slotGrid.EndEdit();
        CaptureCurrentDraft();
        TemplateOption template = SelectedTemplate;
        if (template == null)
        {
            MessageBox.Show(this, "請先選擇圖型。", "資料不完整", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return false;
        }

        var selections = new List<SlotSelection>();
        var supported = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            ".csv", ".tsv", ".txt", ".dat", ".xlsx", ".xls", ".json"
        };
        foreach (DataGridViewRow row in slotGrid.Rows)
        {
            var slot = row.Tag as SlotOption;
            if (slot == null)
            {
                continue;
            }
            string dataPath = CellText(row, DataPathColumn).Trim();
            if (!File.Exists(dataPath))
            {
                MessageBox.Show(
                    this,
                    "請為「" + slot.Title + "」選擇存在的資料檔。",
                    "資料不完整",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Warning
                );
                return false;
            }
            if (!supported.Contains(Path.GetExtension(dataPath)))
            {
                MessageBox.Show(
                    this,
                    "「" + slot.Title + "」的資料檔格式不支援。",
                    "資料不完整",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Warning
                );
                return false;
            }
            selections.Add(new SlotSelection
            {
                Id = slot.Id,
                DataPath = Path.GetFullPath(dataPath),
                SampleName = slot.UsesSampleName ? CellText(row, SampleNameColumn).Trim() : ""
            });
        }

        string outputPath = outputBox.Text.Trim();
        if (String.IsNullOrWhiteSpace(outputPath))
        {
            SuggestOutput(true);
            outputPath = outputBox.Text.Trim();
        }
        if (String.IsNullOrWhiteSpace(outputPath))
        {
            MessageBox.Show(this, "請選擇輸出專案位置。", "資料不完整", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return false;
        }
        if (!String.Equals(Path.GetExtension(outputPath), ".opju", StringComparison.OrdinalIgnoreCase))
        {
            outputPath = Path.ChangeExtension(outputPath, ".opju");
            outputBox.Text = outputPath;
        }
        outputPath = Path.GetFullPath(outputPath);
        foreach (SlotSelection selection in selections)
        {
            if (String.Equals(selection.DataPath, outputPath, StringComparison.OrdinalIgnoreCase))
            {
                MessageBox.Show(this, "輸出專案不能覆寫輸入資料檔。", "資料不完整", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return false;
            }
        }

        request = new RenderRequest
        {
            TemplateId = template.Id,
            Slots = selections,
            SlotCount = selections.Count,
            OutputPath = outputPath,
            HiddenOrigin = hiddenCheck.Checked
        };
        if (template.Id == "CHARGE_AND_DISCHARGE")
        {
            if (electrodeWeightBox.Value <= foilWeightBox.Value)
            {
                MessageBox.Show(this, "請輸入重量：極片重量必須大於鋁箔重量（mg）。", "重量不完整", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return false;
            }
            request.ElectrodeWeightMg = electrodeWeightBox.Value;
            request.FoilWeightMg = foilWeightBox.Value;
            request.ExcelSheet = batterySheetBox.SelectedItem == null ? "" : Convert.ToString(batterySheetBox.SelectedItem);
            request.CapacityUnit = Convert.ToString(capacityUnitBox.SelectedItem);
        }
        return true;
    }

    private static string CellText(DataGridViewRow row, int index)
    {
        object value = row.Cells[index].Value;
        return value == null ? "" : Convert.ToString(value);
    }

    private void StartRender(object sender, EventArgs e)
    {
        RenderRequest request;
        if (worker.IsBusy || !TryBuildRequest(out request))
        {
            return;
        }
        string parent = Path.GetDirectoryName(request.OutputPath);
        if (!Directory.Exists(parent))
        {
            Directory.CreateDirectory(parent);
        }
        lastOutput = request.OutputPath;
        SetBusy(true);
        statusLabel.Text = "正在使用 " + request.TemplateId + " 出圖，請稍候…";
        worker.RunWorkerAsync(request);
    }

    private void RenderInBackground(object sender, DoWorkEventArgs e)
    {
        var request = (RenderRequest)e.Argument;
        e.Result = ExecuteRender(request);
    }

    private RenderResult ExecuteRender(RenderRequest request)
    {
        string manifestPath = Path.Combine(
            Path.GetTempPath(),
            "origin_plotter_ui_" + Guid.NewGuid().ToString("N") + ".json"
        );
        try
        {
            string python = FindPython();
            string bridge = Path.Combine(appDirectory, "origin_ui_bridge.py");
            string vendor = Path.Combine(appDirectory, "vendor");
            if (!File.Exists(bridge))
            {
                throw new FileNotFoundException("找不到 origin_ui_bridge.py。", bridge);
            }

            var slotRows = new List<Dictionary<string, object>>();
            foreach (SlotSelection selection in request.Slots)
            {
                slotRows.Add(new Dictionary<string, object>
                {
                    { "id", selection.Id },
                    { "data", selection.DataPath },
                    { "sample_name", selection.SampleName }
                });
            }
            var manifest = new Dictionary<string, object>
            {
                { "template", request.TemplateId },
                { "output", request.OutputPath },
                { "hidden", request.HiddenOrigin },
                { "slot_count", request.SlotCount },
                { "slots", slotRows }
            };
            if (request.TemplateId == "CHARGE_AND_DISCHARGE")
            {
                manifest["electrode_weight_mg"] = request.ElectrodeWeightMg;
                manifest["foil_weight_mg"] = request.FoilWeightMg;
                manifest["excel_sheet"] = request.ExcelSheet;
                manifest["capacity_unit"] = request.CapacityUnit;
            }
            var serializer = new JavaScriptSerializer();
            File.WriteAllText(manifestPath, serializer.Serialize(manifest), new UTF8Encoding(false));

            var arguments = new StringBuilder();
            arguments.Append(Quote(bridge));
            arguments.Append(" --job ");
            arguments.Append(Quote(manifestPath));

            var info = new ProcessStartInfo();
            info.FileName = python;
            info.Arguments = arguments.ToString();
            info.WorkingDirectory = appDirectory;
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.RedirectStandardOutput = true;
            info.RedirectStandardError = true;
            info.StandardOutputEncoding = Encoding.UTF8;
            info.StandardErrorEncoding = Encoding.UTF8;
            info.EnvironmentVariables["PYTHONUTF8"] = "1";
            info.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"] = "1";
            string currentPythonPath = info.EnvironmentVariables["PYTHONPATH"] ?? "";
            info.EnvironmentVariables["PYTHONPATH"] = String.IsNullOrEmpty(currentPythonPath)
                ? vendor
                : vendor + ";" + currentPythonPath;

            using (Process process = Process.Start(info))
            {
                string output = process.StandardOutput.ReadToEnd();
                string error = process.StandardError.ReadToEnd();
                process.WaitForExit();
                if (process.ExitCode != 0)
                {
                    string message = String.IsNullOrWhiteSpace(error) ? output : error;
                    return new RenderResult
                    {
                        Success = false,
                        Message = message.Trim(),
                        OutputPath = request.OutputPath
                    };
                }
            }
            if (!File.Exists(request.OutputPath))
            {
                return new RenderResult
                {
                    Success = false,
                    Message = "程式已結束，但找不到輸出專案：\r\n" + request.OutputPath,
                    OutputPath = request.OutputPath
                };
            }
            return new RenderResult
            {
                Success = true,
                Message = "完成",
                OutputPath = request.OutputPath
            };
        }
        catch (Exception exception)
        {
            return new RenderResult
            {
                Success = false,
                Message = exception.Message,
                OutputPath = request.OutputPath
            };
        }
        finally
        {
            try
            {
                if (File.Exists(manifestPath))
                {
                    File.Delete(manifestPath);
                }
            }
            catch
            {
            }
        }
    }

    private string FindPython()
    {
        string bundled = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "runtime", "python.exe");
        if (File.Exists(bundled)) return bundled;
        throw new FileNotFoundException("找不到內附 Python。請完整解壓縮應用程式資料夾，不要單獨移動 EXE。");
    }

    private static string Quote(string value)
    {
        return "\"" + value.Replace("\"", "\\\"") + "\"";
    }

    private void RenderCompleted(object sender, RunWorkerCompletedEventArgs e)
    {
        SetBusy(false);
        if (e.Error != null)
        {
            statusLabel.Text = "出圖失敗。";
            MessageBox.Show(this, e.Error.Message, "出圖失敗", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return;
        }
        var result = (RenderResult)e.Result;
        if (!result.Success)
        {
            statusLabel.Text = "出圖失敗，請查看錯誤訊息。";
            MessageBox.Show(this, result.Message, "出圖失敗", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return;
        }
        lastOutput = result.OutputPath;
        statusLabel.Text = "完成：" + Path.GetFileName(result.OutputPath);
        openButton.Enabled = true;
        MessageBox.Show(this, "專案已產生：\r\n" + result.OutputPath, "出圖完成", MessageBoxButtons.OK, MessageBoxIcon.Information);
    }

    private void SetBusy(bool busy)
    {
        batteryPanel.Enabled = !busy;
        templateBox.Enabled = !busy;
        slotCountBox.Enabled = !busy;
        slotGrid.Enabled = !busy;
        outputBox.Enabled = !busy;
        outputBrowseButton.Enabled = !busy;
        hiddenCheck.Enabled = !busy;
        generateButton.Enabled = !busy;
        openButton.Enabled = !busy && !String.IsNullOrWhiteSpace(lastOutput) && File.Exists(lastOutput);
        progress.Visible = busy;
    }

    private void OpenOutput(object sender, EventArgs e)
    {
        if (String.IsNullOrWhiteSpace(lastOutput) || !File.Exists(lastOutput))
        {
            MessageBox.Show(this, "目前沒有可開啟的輸出專案。", "找不到專案", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return;
        }
        Process.Start(new ProcessStartInfo(lastOutput) { UseShellExecute = true });
    }

    public Dictionary<string, object> RunUiSelfTest(string previewPath)
    {
        Show();
        Application.DoEvents();

        SelectTemplateForTest("XRD1");
        Dictionary<string, object> xrd1 = CaptureSlotLayout();
        SelectTemplateForTest("XRD2");
        Dictionary<string, object> xrd2 = CaptureSlotLayout();
        SelectTemplateForTest("XRD3");
        Dictionary<string, object> xrd3 = CaptureSlotLayout();
        SelectTemplateForTest("CHARGE_AND_DISCHARGE");
        Dictionary<string, object> battery = CaptureSlotLayout();
        bool batteryControlsVisible = batteryPanel.Visible && electrodeWeightBox.Visible && foilWeightBox.Visible;
        bool batteryWorkbookTest = foilWeightBox.Value == 7.4M;
        bool portableRenderTest = true;
        SuggestOutput(true);
        bool desktopOutputTest = Path.GetDirectoryName(outputBox.Text) == Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
        string batteryTestFile = Environment.GetEnvironmentVariable("ORIGIN_BATTERY_SELFTEST_FILE");
        if (!String.IsNullOrWhiteSpace(batteryTestFile))
        {
            slotGrid.Rows[0].Cells[DataPathColumn].Value = batteryTestFile;
            RefreshBatterySheets();
            batteryWorkbookTest = batterySheetBox.Items.Count == 2;
            batterySheetBox.SelectedIndex = 1;
            electrodeWeightBox.Value = 10;
            foilWeightBox.Value = 8;
            RenderRequest testRequest = null;
            batteryWorkbookTest = batteryWorkbookTest && TryBuildRequest(out testRequest)
                && testRequest.ExcelSheet == Convert.ToString(batterySheetBox.Items[1])
                && testRequest.ElectrodeWeightMg == 10 && testRequest.FoilWeightMg == 8
                && testRequest.CapacityUnit == "Ah";
            string portableTestOutput = Environment.GetEnvironmentVariable("ORIGIN_APP_RENDER_SELFTEST");
            if (batteryWorkbookTest && !String.IsNullOrWhiteSpace(portableTestOutput))
            {
                testRequest.OutputPath = portableTestOutput;
                RenderResult portableResult = ExecuteRender(testRequest);
                portableRenderTest = portableResult.Success;
                if (!portableResult.Success) throw new InvalidOperationException(portableResult.Message);
            }
        }
        SelectTemplateForTest("C_RATE");
        Dictionary<string, object> cRateDefault = CaptureSlotLayout();
        SetSlotCountForTest(1);
        Dictionary<string, object> cRateOne = CaptureSlotLayout();
        SelectTemplateForTest("FTIR");
        Dictionary<string, object> ftir = CaptureSlotLayout();
        bool filenameDefaultsTest = true;
        int filenamePagesTested = 0;
        foreach (TemplateOption option in templateBox.Items)
        {
            SelectTemplateForTest(option.Id);
            for (int i = 0; i < slotGrid.Rows.Count; i++)
            {
                DataGridViewRow row = slotGrid.Rows[i];
                string file = "樣品_" + option.Id + "_" + (i + 1) + ".xlsx";
                row.Cells[DataPathColumn].Value = Path.Combine(Path.GetTempPath(), file);
                SetFileSampleName(row);
                filenameDefaultsTest = filenameDefaultsTest
                    && CellText(row, SampleNameColumn) == Path.GetFileNameWithoutExtension(file)
                    && !row.Cells[SampleNameColumn].ReadOnly;
            }
            slotGrid.Rows[0].Cells[SampleNameColumn].Value = "自訂名稱";
            CaptureCurrentDraft();
            RebuildSlotRows();
            filenameDefaultsTest = filenameDefaultsTest && CellText(slotGrid.Rows[0], SampleNameColumn) == "自訂名稱";
            filenamePagesTested++;
        }
        SelectTemplateForTest("CHARGE_AND_DISCHARGE");
        if (!String.IsNullOrWhiteSpace(previewPath))
        {
            string parent = Path.GetDirectoryName(Path.GetFullPath(previewPath));
            if (!Directory.Exists(parent))
            {
                Directory.CreateDirectory(parent);
            }
            using (var bitmap = new Bitmap(Width, Height))
            {
                DrawToBitmap(bitmap, new Rectangle(0, 0, Width, Height));
                bitmap.Save(previewPath, System.Drawing.Imaging.ImageFormat.Png);
            }
        }

        bool passed = Convert.ToInt32(xrd1["row_count"]) == 3
            && Convert.ToInt32(xrd2["row_count"]) == 4
            && Convert.ToInt32(xrd3["row_count"]) == 2
            && Convert.ToInt32(battery["row_count"]) == 1
            && batteryControlsVisible
            && batteryWorkbookTest
            && desktopOutputTest
            && filenameDefaultsTest && filenamePagesTested == 11
            && portableRenderTest
            && Convert.ToInt32(cRateDefault["row_count"]) == 3
            && Convert.ToInt32(cRateOne["row_count"]) == 1
            && Convert.ToInt32(ftir["row_count"]) == 6
            && Convert.ToBoolean(ftir["slot_count_visible"])
            && Convert.ToInt32(ftir["slot_count_max"]) == 6;
        Close();

        return new Dictionary<string, object>
        {
            { "pass", passed },
            { "xrd1", xrd1 },
            { "xrd2", xrd2 },
            { "xrd3", xrd3 },
            { "battery", battery },
            { "battery_weights_visible", batteryControlsVisible },
            { "battery_workbook_test", batteryWorkbookTest },
            { "foil_default_mg", 7.4M },
            { "desktop_output_test", desktopOutputTest },
            { "filename_defaults_test", filenameDefaultsTest },
            { "filename_pages_tested", filenamePagesTested },
            { "portable_render_test", portableRenderTest },
            { "c_rate_default", cRateDefault },
            { "c_rate_one", cRateOne },
            { "ftir", ftir },
            { "preview", previewPath ?? "" }
        };
    }

    private void SetSlotCountForTest(int count)
    {
        if (!slotCountBox.Visible)
        {
            throw new InvalidOperationException("目前模板沒有合併數量控制。 ");
        }
        slotCountBox.Value = count;
        Application.DoEvents();
    }

    private void SelectTemplateForTest(string templateId)
    {
        for (int index = 0; index < templateBox.Items.Count; index++)
        {
            var option = templateBox.Items[index] as TemplateOption;
            if (option != null && String.Equals(option.Id, templateId, StringComparison.OrdinalIgnoreCase))
            {
                templateBox.SelectedIndex = index;
                Application.DoEvents();
                return;
            }
        }
        throw new InvalidOperationException("UI 找不到模板：" + templateId);
    }

    private Dictionary<string, object> CaptureSlotLayout()
    {
        var rows = new List<Dictionary<string, object>>();
        foreach (DataGridViewRow row in slotGrid.Rows)
        {
            var slot = row.Tag as SlotOption;
            rows.Add(new Dictionary<string, object>
            {
                { "id", slot == null ? "" : slot.Id },
                { "title", CellText(row, SlotTitleColumn) },
                { "default_name", CellText(row, SampleNameColumn) },
                { "data_editable", !row.Cells[DataPathColumn].ReadOnly },
                { "sample_name_editable", !row.Cells[SampleNameColumn].ReadOnly }
            });
        }
        return new Dictionary<string, object>
        {
            { "template", SelectedTemplate == null ? "" : SelectedTemplate.Id },
            { "row_count", rows.Count },
            { "slot_count_visible", slotCountBox.Visible },
            { "slot_count", slotCountBox.Visible ? Convert.ToInt32(slotCountBox.Value) : rows.Count },
            { "slot_count_min", slotCountBox.Visible ? Convert.ToInt32(slotCountBox.Minimum) : rows.Count },
            { "slot_count_max", slotCountBox.Visible ? Convert.ToInt32(slotCountBox.Maximum) : rows.Count },
            { "rows", rows }
        };
    }

    private void PreventCloseWhileRendering(object sender, FormClosingEventArgs e)
    {
        if (worker.IsBusy)
        {
            e.Cancel = true;
            MessageBox.Show(this, "Origin 尚在出圖，完成後才能關閉介面。", "正在出圖", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }
    }
}

internal static class OriginPlotterWindowsUI
{
    [STAThread]
    private static int Main(string[] args)
    {
        bool selfTest = args.Length >= 2
            && String.Equals(args[0], "--self-test", StringComparison.OrdinalIgnoreCase);
        try
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            if (selfTest)
            {
                string previewPath = args.Length >= 3 ? args[2] : "";
                Dictionary<string, object> result;
                using (var form = new OriginPlotterForm())
                {
                    result = form.RunUiSelfTest(previewPath);
                }
                var serializer = new JavaScriptSerializer();
                File.WriteAllText(args[1], serializer.Serialize(result), new UTF8Encoding(false));
                return Convert.ToBoolean(result["pass"]) ? 0 : 2;
            }
            Application.Run(new OriginPlotterForm());
            return 0;
        }
        catch (Exception exception)
        {
            if (selfTest)
            {
                try
                {
                    var serializer = new JavaScriptSerializer();
                    File.WriteAllText(
                        args[1],
                        serializer.Serialize(new Dictionary<string, object>
                        {
                            { "pass", false },
                            { "error", exception.ToString() }
                        }),
                        new UTF8Encoding(false)
                    );
                }
                catch
                {
                }
                return 1;
            }
            MessageBox.Show(
                exception.Message,
                "Origin 出圖介面無法啟動",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error
            );
            return 1;
        }
    }
}
