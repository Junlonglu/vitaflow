import request from "./request"

export type AvatarItem = {
  id: number
  /** 存储对象名（`avatar/1_xxx.jpg`），简历里引用的就是它 */
  object_name: string
  /** 前端可直接用的相对地址 */
  url: string
  file_name: string
  content_type?: string | null
  file_size?: number | null
  /** 有多少份简历正在用这张头像 */
  usage_count: number
  create_time: string
}

export type AvatarLibrary = {
  items: AvatarItem[]
  total: number
  limit: number
}

export const listAvatarsApi = () => request.get<AvatarLibrary, AvatarLibrary>("/avatars")

export const deleteAvatarApi = (id: number) => request.delete(`/avatars/${id}`)
