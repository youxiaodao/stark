#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2018/9/28 11:43
# @Author  : DollA
# @File    : stark.py
# @Software: PyCharm
from django.conf.urls import url
from django.shortcuts import HttpResponse, render, redirect
import functools
from types import FunctionType
from django.utils.safestring import mark_safe
from django.urls import reverse
from django import forms
from django.db.models import Q
from django.http import QueryDict
from django.db.models.fields.related import ForeignKey, ManyToManyField
from stark.utils.pagination import Pagination


class ModelConfigMapping(object):
    """
    封装用于模型类用于注册的属性
    """
    def __init__(self, model, config, prev):
        """

        :param model: 表模型本身
        :param config: 配置类
        :param prev: 扩展配置类的标识，如pri表示私有，pub表示公共
        """
        self.model = model
        self.config = config
        self.prev = prev


# 获取选择文本值函数
def get_chioce_text(head, field):
    """
    在数据列表页面，用于显示choice field的文本信息
    :param head:表头名称
    :param field: 字段名称
    :return:
    """

    def inner(self,row=None, isHeader=False):
        if isHeader:
            return head
        func_name = 'get_%s_display' % field
        return getattr(row, func_name)()

    return inner


# 组合搜索显示每一行
class Row(object):
    def __init__(self, data_list, option, query_dict):
        """

        :param data_list: 元组或者Queryset,包含了数据库对象或者选择项，什么时候是与元组？什么时候是QuerySet
        :param option: option对象
        :param query_dict: request.GET请求
        """
        self.data_list = data_list
        self.option = option
        self.query_dict = query_dict

    def __iter__(self):
        yield '<div class="whole">'

        total_query_dict = self.query_dict.copy()
        total_query_dict._mutable = True

        origin_value_list = self.query_dict.getlist(self.option.field)  # ['2',]
        if origin_value_list:
            total_query_dict.pop(self.option.field)
            yield '<a href="?%s">全部</a>' % total_query_dict.urlencode()
        else:
            yield '<a class="active" href="?%s">全部</a>' % total_query_dict.urlencode()
        yield '</div>'
        yield '<div class="others">'

        for item in self.data_list:  # item queryset中的一个对象
            val = str(self.option.get_value(item))
            text = self.option.get_text(item)

            query_dict = self.query_dict.copy()
            query_dict._mutable = True

            if not self.option.isMulti:
                if val in origin_value_list:
                    query_dict.pop(self.option.field)
                    yield '<a class="active" href="?%s">%s</a>' % (query_dict.urlencode(), text)
                else:
                    query_dict[self.option.field] = val
                    yield '<a href="?%s">%s</a>' % (query_dict.urlencode(), text)

            else:
                # 多选
                # 当前field选中值
                multi_val_list = query_dict.getlist(self.option.field)
                if val in origin_value_list:
                    # 特殊情况，有自己的值
                    multi_val_list.remove(val)
                    # 重新赋值
                    query_dict.setlist(self.option.field, multi_val_list)
                    yield '<a class="active" href="?%s">%s</a>' % (query_dict.urlencode(), text)
                else:
                    # 添加选中项
                    multi_val_list.append(val)
                    query_dict.setlist(self.option.field, multi_val_list)
                    yield '<a href="?%s">%s</a>' % (query_dict.urlencode(), text)

        yield '</div>'


# 组合搜索配置项
class Option(object):
    def __init__(self, field, condition=None, text_func=None, value_func=None, isChoice=False, isMulti=False,
                 isFk=False):
        self.field = field
        self.isChoice = isChoice
        if not condition:
            condition = {}
        self.condition = condition
        self.text_func = text_func
        self.value_func = value_func
        self.isMulti = isMulti
        self.isFk = isFk

    def get_queryset(self, _field, model_class, query_dict):
        """
        获取数据列表
        :param _field: 数据库类的行对象
        :param model_class: 类本身
        :param query_dict: request.GET请求
        :return:
        """
        if isinstance(_field, ForeignKey) or isinstance(_field, ManyToManyField):
            row = Row(_field.remote_field.model.objects.filter(**self.condition), self, query_dict)
        else:
            if self.isChoice:
                row = Row(_field.choices, self, query_dict)
            else:
                row = Row(model_class.objects.filter(**self.condition), self, query_dict)
        return row

    def get_text(self, item):
        """
        获取组合搜索显示的文本内容
        如果定制了text_func,就用;
        如果没有定制，就显示对象
        :param item:显示出来的queryset中的每一个数据
        :return:
        """
        if self.text_func:
            return self.text_func(item)
        return str(item)

    def get_value(self, item):
        """
        获取组合搜索显示文本对应的主键值，
        包装到URL中，为搜索提供条件
        :param item:
        :return:
        """
        if self.value_func:
            return self.value_func(item)
        if self.isChoice:
            return item[0]

        return item.pk


class ChangeListParameters(object):
    """
    封装列表页面需要的所有功能
    """

    def __init__(self, config, queryset, search_list, search_condition, con):
        self.config = config
        # 批量操作功能
        action_func_list = config.get_action_list()
        self.action_list = [{'text': func.text, 'name': func.__name__} for func in action_func_list]
        # 添加按钮
        self.add_btn = config.get_add_btn('add')
        # 查询得出的数据对象列表
        self.queryset = queryset
        # 表格要显示的列标题
        self.list_display = config.get_list_display()
        # # 搜索用的参数打包进来
        # self.search_list=search_list
        # self.search_condition=search_condition
        # self.con=con
        self.list_filter = config.get_filter_list()

    def gen_list_filter_rows(self):
        """
        获取每一行搜索关键词列表
        :return:
        """
        ######### 组合搜索 #########
        # 显示每一行搜索关键词列表
        for option in self.list_filter:
            _field = self.config.model_class._meta.get_field(option.field)
            # 传入参数--行对象，数据类本身，GET请求
            row = option.get_queryset(_field, self.config.model_class, self.config.request.GET)
            yield row


class StarkConfig(object):
    order_by = []  # 排序规则.可以默认给一个id，但是，不是每个表都有id？！！默认设置空
    # 也可以在自己的APP中的stark使用钩子
    list_display = []  # 默认的表头,默认的不应该有值，那么应该作何展示？？！！

    model_form_class = None  # 为自定义留下接口，MordelForm

    ###########对应批量操作功能的函数##########

    def multi_delete(self, request):
        """
        批量删除
        :return:
        """
        pk_list = request.POST.getlist('pk')
        self.model_class.objects.filter(pk__in=pk_list).delete()

    def multi_init(self, request):
        """
        初始化
        :return:
        """

        pass

    multi_delete.text = '批量删除'
    multi_init.text = '初始化'

    action_list = []  # 批量操作功能列表,默认为空
    search_list = []  # 定制搜索范围，即那一列
    filter_list = []  # 用于组合搜索显示的条件

    def __init__(self, model_class, site, prev):
        self.model_class = model_class
        self.site = site
        self.prev = prev
        self.request = None
        self.back_condition_key = "_filter"  # 保留之前的搜索条件用的key
        self.request = None

    # 装饰器
    def wrapper(self, func):
        """
        为视图函数执行之前或者之后，预留钩子函数
            wraps-->为了保证在调用func时，保留函数原信息

        :param func: 对应的视图函数
        :return:
        """
        @functools.wraps(func)
        def inner(request, *args, **kwargs):
            self.request = request
            return func(request, *args, **kwargs)

        return inner

    ###################页面显示自定制功能checkbox、编辑、删除#################
    def display_checkbox(self, row=None, isHeader=False):
        """

        :param row: 每一行数据对象，在templatetags中迭代queryset获取每一行数据
        :param isHeader: 布尔值，是否是表头
        :return:字符串标签，作为每一行数据，利用templatetags渲染到前端
        """
        if isHeader:
            return "选择"
        return mark_safe("<input type='checkbox' name='pk' value='%s'/>" % row.pk)

    def display_edit(self, row=None, isHeader=False):
        if isHeader:
            return "编辑"

        reverse_url_edit = self.reverse_url('change', row=row)

        return mark_safe('<a href="%s"><i class="fa fa-edit" aria-hidden="true"></i></a>' % reverse_url_edit)

    def display_del(self, row=None, isHeader=False):
        if isHeader:
            return "删除"

        reverse_url_del = self.reverse_url('del', row=row)

        return mark_safe('<a href="%s"><i class="fa fa-trash-o" aria-hidden="true"></i></a>' % reverse_url_del)

    def display_del_edit(self, row=None, isHeader=False):
        if isHeader:
            return "操作"
        reverse_url_edit = self.reverse_url('change', row=row)
        reverse_url_del = self.reverse_url('del', row=row)
        del_edit_tag = """<a href="%s"><i class="fa fa-edit" aria-hidden="true"></i></a></a>&nbsp;&nbsp;|
        &nbsp;<a href="%s"><i class="fa fa-trash-o" aria-hidden="true"></i></a>
        """ % (reverse_url_edit, reverse_url_del)
        return mark_safe(del_edit_tag)

    # 排序规则
    def get_order_by(self):
        return self.order_by

    # 获取定义表头的显示，显示哪些列的数据
    def get_list_display(self):
        val = []
        val.extend(self.list_display)
        val.append(StarkConfig.display_del_edit)  # 不能用self

        return val

    ############################所有表的增删改查都在这里###########################
    # 添加按钮
    def get_add_btn(self, display_type):
        reverse_url_add = self.reverse_url(display_type)
        return mark_safe('<a href="%s" class="btn btn-success">添加</a>' % reverse_url_add)

    # 获取批量操作功能列表
    def get_action_list(self):
        val = []
        val.extend(self.action_list)
        return val

    def get_action_dict(self):
        """
        生产用于过滤非法请求的字典
        :return:
        """
        val = {}
        for item in self.action_list:
            val[item.__name__] = item
        return val

    # 获取搜索列，即搜索范围
    def get_search_list(self):
        val = []
        val.extend(self.search_list)
        return val

    # 获取搜索框条件
    def get_search_condition(self, request):
        search_list = self.get_search_list()
        search_condition = request.GET.get('q', '').strip()
        # 连接条件
        con = Q()
        con.connector = 'OR'
        if search_condition:
            for field in search_list:
                con.children.append(('%s__contains' % field, search_condition))
        return search_list, search_condition, con

    # 获取组合搜索关键字
    def get_filter_list(self):
        val = []
        val.extend(self.filter_list)
        return val

    # 获取用户点击组合搜索条件
    def get_list_filter_condition(self):
        # 组合搜素,再加一次filter
        filter_list = self.get_filter_list()
        comb_condition = {}
        for option in filter_list:
            element = self.request.GET.getlist(option.field)
            if element:
                comb_condition['%s__in' % option.field] = element
        return comb_condition

    # 为公户和私户拆分
    def get_queryset(self):
        return self.model_class.objects

    def changelist_view(self, request):
        """
        数据表格的处理和展示
        :param request:
        :return:
        """
        ####批量操作Action####
        if request.method == 'POST':
            action_name = request.POST.get('action')
            # 过滤非法请求
            action_dict = self.get_action_dict()
            if action_name not in action_dict:
                return HttpResponse('非法请求')

            response = getattr(self, action_name)(request)
            if response:
                return response

        ####关键字搜索####
        search_list, search_condition, con = self.get_search_condition(request)

        #####处理分页#####
        page = self.request.GET.get('page')
        obj_tatal_count = self.model_class.objects.filter(con).count()
        # 原条件URL，直接copy后传递过去，默认不能修改
        query_params = request.GET.copy()
        # 设置_mutable，或者用QueryDict方法转换成字典，以便修改
        query_params._mutable = True
        page = Pagination(page, obj_tatal_count, request.path_info, query_params, per_page=7)
        # 表格要显示的数据对象列表
        orign_queryset = self.get_queryset()
        queryset = orign_queryset.filter(con).filter(**self.get_list_filter_condition()).order_by(
            *self.get_order_by()).distinct()[page.start:page.end]
        # 传参数类对象的应用
        change_list_parameter = ChangeListParameters(self, queryset, search_list, search_condition, con)

        return render(request, 'stark/changelist.html', locals())

    # 获取modelForm类
    def get_model_form_class(self):
        if self.model_form_class:
            return self.model_form_class

        class AddModelForm(forms.ModelForm):
            class Meta:
                model = self.model_class
                fields = "__all__"

        return AddModelForm

    def save(self, form, modify=False):
        """

        :param form:
        :param modify: False是新增，True是修改
        :return:
        """

        return form.save()

    def add_view(self, request):
        """
        所有添加页面，都在此函数处理
        :param request:
        :return:
        """
        AddModelForm = self.get_model_form_class()
        if request.method == 'POST':
            form = AddModelForm(request.POST)
            if form.is_valid():
                self.save(form, modify=False)
                reserve_url_list = self.reverse_url('changelist')
                return redirect(reserve_url_list)
        form = AddModelForm()

        return render(request, 'stark/change.html', locals())

    def change_view(self, request, pk):
        """
        编辑视图
        :param request:
        :param pk:对象PK
        :return:
        """
        obj = self.model_class.objects.filter(pk=pk).first()
        if not obj:
            return HttpResponse('数据不存在')
        ModelForm = self.get_model_form_class()
        form = ModelForm(instance=obj)
        if request.method == 'POST':
            form = ModelForm(request.POST, instance=obj)
            if form.is_valid():
                # modify表示，是不是修改
                self.save(form, modify=True)
                reverse_url_list = self.reverse_url('changelist')

                return redirect(reverse_url_list)

        return render(request, 'stark/change.html', locals())

    def delete_view(self, request, pk):
        """
        删除方案一：
            get请求：
                点击列表中的删除按钮显示选择删除或者取消的页面
                点击取消，回到列表
            post请求：
                点击确定，删除后，回到列表
        删除方案二：模态对话框

        本次采用方案一
        :param request:
        :param pk: 对象的pk值
        :return:
        """
        reverse_url_list = self.reverse_url('changelist')
        if request.method == 'POST':
            self.model_class.objects.filter(pk=pk).delete()
            return redirect(reverse_url_list)
        return render(request, 'stark/delete.html', locals())

    # 获取url的别名name
    def get_url_name(self, display_type):
        """
        从self中获取表模型类，
        :param display_type: 显示类型，如删除del
        :return:
        """
        app_label = self.model_class._meta.app_label
        model_name = self.model_class._meta.model_name
        if self.prev:
            name = '%s_%s_%s_%s' % (app_label, model_name, self.prev, display_type)
        else:
            name = '%s_%s_%s' % (app_label, model_name, display_type)
        return name

    # 定义了基本的增删改查URL
    def get_urls(self):
        """
        提供增删改车基本的4个URL
        :return:
        """

        urlpatterns = [
            url(r'^list/$', self.wrapper(self.changelist_view), name=self.get_url_name('changelist')),
            url(r'^add/$', self.wrapper(self.add_view), name=self.get_url_name('add')),
            url(r'^(?P<pk>\d+)/change/$', self.wrapper(self.change_view), name=self.get_url_name('change')),
            url(r'^(?P<pk>\d+)/del/$', self.wrapper(self.delete_view), name=self.get_url_name('del')),
        ]

        # 扩展url
        extra = self.extra_url()
        if extra:
            urlpatterns.extend(extra)

        return urlpatterns

    # 扩展URL
    def extra_url(self):
        """
        自定义添加其他URL方法
        :return:
        """
        pass

    # 反向解析URL
    def reverse_url(self, display_type, row=None):
        """

        :param display_type: url的类型，如删除del、编辑change...
        :param row: 一行数据对象
        :return:
        """
        namespace = self.site.namespace

        name = '%s:%s' % (namespace, self.get_url_name(display_type))
        if row:
            reverse_url = reverse(name, kwargs={'pk': row.pk})
        else:
            reverse_url = reverse(name)
        # 获取跳转前的搜索条件,并保留
        if display_type == 'changelist':
            origin_condition = self.request.GET.get(self.back_condition_key)
            if not origin_condition:
                return reverse_url
            reverse_url_with_search_condition = '%s?%s' % (reverse_url, origin_condition)
            return reverse_url_with_search_condition
        if not self.request.GET:
            return reverse_url
        param_str = self.request.GET.urlencode()
        new_query_dict = QueryDict(mutable=True)
        new_query_dict[self.back_condition_key] = param_str

        reverse_url_with_search_condition = '%s?%s' % (reverse_url, new_query_dict.urlencode())
        return reverse_url_with_search_condition

    @property
    def urls(self):
        return self.get_urls()


class AdminSite(object):
    """
    注册类

    """

    def __init__(self):
        self._registry = []
        self.app_name = 'stark'
        self.namespace = 'stark'

    def registry(self, model_class, stark_config=None, prev=None):
        """
        用与注册类的方法
        :param model_class:需要注册的类
        :param stark_config: 封装了基本URL和增删改查方法的类
        :param prev:当我们需要对同一个model自定制几个不同的config时使用的变量，例子:pri表示私有，pub表示公共
        :return:
        """
        if not stark_config:
            stark_config = StarkConfig
        # ModelConfigMapping 封装注册对象的属性，表模型类、config配置类、自定制prev名
        self._registry.append(ModelConfigMapping(model_class, stark_config(model_class, self, prev), prev))

    def get_urls(self):
        """
        1、获取APP名、类名小写、prev（非必须）
        2、拼接成路径，
        3、利用元组形式的include，加入固定的增删改查路由关系，拼接出每个表独有的增删改查路径
        :return:
        """
        urlpatterns = []

        for item in self._registry:
            app_label = item.model._meta.app_label  # 类所在APP名
            model_name = item.model._meta.model_name  # 类名小写
            if item.prev:
                # (item.config.urls, None, None) 组成一个include，item.config调用config配置内中固定的增删改查URL
                temp = url(r'^%s/%s/%s/' % (app_label, model_name, item.prev), (item.config.urls, None, None))
            else:
                temp = url(r'^%s/%s/' % (app_label, model_name), (item.config.urls, None, None))
            urlpatterns.append(temp)
        return urlpatterns

    @property
    def urls(self):
        """
        提供每个表最终形态的URL
        :return:
        """

        return self.get_urls(), self.app_name, self.namespace


site = AdminSite()
