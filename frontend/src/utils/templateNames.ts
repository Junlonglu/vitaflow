/** 模板展示名兜底：接口没返回 name、或模板列表尚未加载时用这份映射。
 *
 * 值必须与后端注册表 `backend/app/services/template_service.py::TEMPLATES` 完全一致：
 * 否则「接口慢一拍」时的名字和加载完成后的名字不一样，用户会以为换了个模板。
 * 这条一致性由 `backend/tests/test_cross_layer_contracts.py` 守着。
 */
export const templateNames: Record<string, string> = {
  classic: "经典单栏",
  tech: "技术岗位",
  modern: "现代双栏",
  blue_timeline: "蓝色时间轴",
  minimal_light: "极简明亮",
  minimal_mono: "极简单色",
  modern_clean: "现代清新",
  elegant_line: "优雅线型",
  editorial_serif: "编辑部衬线",
  executive_panel: "商务简报",
  portfolio_cards: "作品集卡片",
  compact_matrix: "紧凑矩阵",
}
