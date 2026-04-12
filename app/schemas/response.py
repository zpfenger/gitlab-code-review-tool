# app/schemas/response.py
from pydantic import BaseModel, Field
from typing import Generic, TypeVar, Optional, List, Any

T = TypeVar('T')


class ErrorDetail(BaseModel):
    """结构化错误信息"""
    code: str = "ERROR"
    message: str = ""
    details: Optional[Any] = None


class ApiResponse(BaseModel, Generic[T]):
    """统一 API 响应格式"""
    success: bool
    data: Optional[T] = None
    error: Optional[ErrorDetail] = None
    message: Optional[str] = None

    @staticmethod
    def ok(data: T = None, message: str = "") -> "ApiResponse[T]":
        """快捷构造成功响应"""
        return ApiResponse(success=True, data=data, message=message or "Success")

    @staticmethod
    def fail(code: str = "ERROR", message: str = "", details: Any = None) -> "ApiResponse":
        """快捷构造失败响应"""
        return ApiResponse(
            success=False,
            error=ErrorDetail(code=code, message=message, details=details)
        )


class PaginatedResponse(BaseModel, Generic[T]):
    """分页响应格式"""
    success: bool = True
    data: List[T] = []
    total: int = 0
    page: int = 1
    page_size: int = 20
