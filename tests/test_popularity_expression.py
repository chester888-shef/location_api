from django.db.models import ExpressionWrapper, FloatField, Value
from django.db.models.functions import Coalesce, Ln

from locations.models import Location


def test_popularity_expression_matches_specification_without_database(request):
    assert {"db", "transactional_db", "_django_db_helper"}.isdisjoint(request.fixturenames)
    query = Location.objects.with_statistics().query
    wrapper = query.annotations["popularity"]
    assert isinstance(wrapper, ExpressionWrapper)
    assert isinstance(wrapper.output_field, FloatField)

    expression = wrapper.expression
    assert expression.connector == expression.lhs.connector == "+"
    rating, reviews, views = expression.lhs.lhs, expression.lhs.rhs, expression.rhs
    for term, weight in ((rating, 0.5), (reviews, 0.3), (views, 0.2)):
        assert term.connector == "*"
        assert isinstance(term.lhs, Value)
        assert term.lhs.value == weight

    assert isinstance(rating.rhs, Coalesce)
    average, default = rating.rhs.get_source_expressions()
    assert average == query.annotations["rating"]
    assert isinstance(default, Value)
    assert default.value == 0.0

    for term, source in ((reviews, "reviews_count"), (views, "views_7d")):
        assert isinstance(term.rhs, Ln)
        argument, = term.rhs.get_source_expressions()
        assert argument.connector == "+"
        assert isinstance(argument.lhs, Value)
        assert argument.lhs.value == 1.0
        assert argument.rhs == query.annotations[source]
